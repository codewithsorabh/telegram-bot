# ─────────────────────────────────────────────────────────────────────────────
#  bot / bot.py
#
#  Telegram Management Bot Entry Point.
#  Command: python ./bot/bot.py
#
#  Responsibilities:
#    • Bootstrap database tables & migrations
#    • Run Telegram UI Bot (ApplicationBuilder)
#    • Manage Userbot accounts (add via Telethon OTP/2FA, view, delete)
#    • Manage Topics, Destination channels, and Forwarding
#    • Manage Admin Settings & permissions
#    • Broadcast signals to Userbot workers via Redis
# ─────────────────────────────────────────────────────────────────────────────

import sys
import os
import asyncio
import warnings

# Ensure bot dir and project root are on sys.path
_BOT_DIR = os.path.abspath(os.path.dirname(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_BOT_DIR, ".."))
for _p in (_BOT_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from telegram.warnings import PTBUserWarning

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"telegram\.ext\._conversationhandler",
)
warnings.filterwarnings(
    "ignore",
    message=r".*per_message.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    category=PTBUserWarning,
)

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config.logger import LOG
from config.db import BOT_TOKEN, db, r

# ── Models bootstrap ──────────────────────────
from models.config import bootstrap_config_table, check_user_permission
from models.topics import bootstrap_topics_table
from models.users import bootstrap_users_table
from models.forwarding_state import bootstrap_forwarding_state_table
from models.forwarding_schedule import bootstrap_forwarding_schedule_table
from models.channel_joins import bootstrap_channel_joins_table

# ── User handlers ─────────────────────────────
from handlers.users.add_user import (
    build_add_user_conv,
    _edit_bubble as _user_edit_bubble,
    _cleanup as _user_cleanup,
    _KEY_MSG_ID,
)
from handlers.users.view_user import (
    VIEW_USERS_HANDLERS,
    build_user_edit_delay_conv,
    build_user_edit_source_conv,
    build_user_edit_channel_conv,
)

# ── Topic handlers ────────────────────────────
from handlers.topics.add_topic import build_add_topic_conv
from handlers.topics.view_topic import VIEW_TOPIC_HANDLERS, build_view_topic_conv

# ── Settings handlers ─────────────────────────
from handlers.settings.manage_settings import (
    SETTINGS_HANDLERS,
    build_manage_settings_conv,
)

# ── Utilities / templates ─────────────────────
from utils.auto_delete import schedule_delete
from templates.messages import (
    WELCOME_MSG,
    CANCELLED_MSG,
)
from templates.inlinebutton import (
    WELCOME_KEYBOARD,
    CANCELLED_KEYBOARD_USER,
)


class TelegramBotApp:
    """
    Manages the Telegram Bot Application lifecycle and registers all handlers.
    """

    def __init__(self):
        self.app: Application | None = None

    def bootstrap_database(self) -> None:
        """Initialize all required tables and auto-migrations."""
        LOG.info("📦 Bootstrapping database schema for Bot...")
        bootstrap_config_table()
        bootstrap_topics_table()
        bootstrap_users_table()
        bootstrap_forwarding_state_table()
        bootstrap_forwarding_schedule_table()
        bootstrap_channel_joins_table()
        LOG.info("✅ Database tables initialized successfully")

    def register_handlers(self) -> None:
        app = self.app

        async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await check_user_permission(update):
                return
            user = update.effective_user
            schedule_delete(context, update.message.chat_id, update.message.message_id)
            sent = await update.message.reply_html(
                WELCOME_MSG(user),
                reply_markup=WELCOME_KEYBOARD(user.id if user else 0),
            )
            context.user_data[_KEY_MSG_ID] = sent.message_id
            context.user_data["au_chat_id"] = sent.chat_id
            schedule_delete(context, sent.chat_id, sent.message_id)

        async def global_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.callback_query
            await query.answer("Cancelled — no data saved.")
            if context.user_data.get(_KEY_MSG_ID):
                await _user_edit_bubble(
                    context, CANCELLED_MSG, CANCELLED_KEYBOARD_USER()
                )
                await _user_cleanup(context)

        async def global_error_handler(
            update: object, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            import traceback
            from telegram import Update as TGUpdate
            from telegram.error import NetworkError, BadRequest

            err = context.error
            err_str = str(err)

            if "is not modified" in err_str or "There is no text in the message to edit" in err_str:
                return

            if (isinstance(err, NetworkError) and not isinstance(err, BadRequest)) or "getaddrinfo failed" in err_str or "ConnectError" in err_str:
                LOG.warning("🌐 Network Connection Error: %s", err)
                if isinstance(update, TGUpdate) and update.callback_query:
                    try:
                        await update.callback_query.answer(
                            "🌐 Network connection error. Please try again.",
                            show_alert=True,
                        )
                    except Exception:
                        pass
                return

            tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
            LOG.error("Unhandled exception:\n%s", tb)

            if isinstance(update, TGUpdate) and update.callback_query:
                try:
                    await update.callback_query.answer(
                        "⚠️ An error occurred. Please try again.",
                        show_alert=True,
                    )
                except Exception:
                    pass
            elif isinstance(update, TGUpdate) and update.message:
                try:
                    await update.message.reply_text(
                        "⚠️ An internal error occurred. Please try again."
                    )
                except Exception:
                    pass

        async def cb_global_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.callback_query
            if query:
                try:
                    await query.answer()
                except Exception:
                    pass

        # Register core handlers
        app.add_handler(CommandHandler("start", start_cmd), group=0)
        app.add_handler(
            CallbackQueryHandler(cb_global_noop, pattern=r"^noop(:.*)?$"), group=0
        )
        app.add_error_handler(global_error_handler)

        # Conversation handlers
        app.add_handler(build_add_user_conv(), group=1)
        app.add_handler(build_user_edit_delay_conv(), group=1)
        app.add_handler(build_user_edit_source_conv(), group=1)
        app.add_handler(build_user_edit_channel_conv(), group=1)

        app.add_handler(build_add_topic_conv(), group=1)
        app.add_handler(build_view_topic_conv(), group=1)

        app.add_handler(build_manage_settings_conv(), group=1)

        # View / action handlers
        for h in (
            VIEW_USERS_HANDLERS
            + VIEW_TOPIC_HANDLERS
            + SETTINGS_HANDLERS
        ):
            app.add_handler(h, group=2)

    async def _post_shutdown(self, app: Application) -> None:
        LOG.info("🛑 Telegram Bot shutting down...")
        try:
            if db and not db.closed:
                db.close()
                LOG.info("PostgreSQL connection closed")
        except Exception as exc:
            LOG.error("Error closing DB: %s", exc)

        try:
            if r:
                r.close()
                LOG.info("Redis connection closed")
        except Exception as exc:
            LOG.error("Error closing Redis: %s", exc)
        LOG.info("👋 Bot shutdown complete.")

    def run(self) -> None:
        if not BOT_TOKEN:
            LOG.critical("TELEGRAM_BOT_TOKEN environment variable is not set!")
            sys.exit(1)

        self.bootstrap_database()

        self.app = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .post_shutdown(self._post_shutdown)
            .build()
        )

        self.register_handlers()
        print("🤖 Telegram Bot started successfully (Polling...)")
        self.app.run_polling(drop_pending_updates=True)


def main():
    bot = TelegramBotApp()
    bot.run()


if __name__ == "__main__":
    main()
