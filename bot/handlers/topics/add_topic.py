# ─────────────────────────────────────────────
#  handlers / topics / add_topic.py
#
#  ConversationHandler – 3-Step Add Topic flow:
#    Step 1: Enter Topic Key/Name (e.g. "Telegram" -> key: "telegram", name: "Telegram")
#    Step 2: Enter Telegram Destination URLs line-by-line (optional "- interval")
#    Step 3: Summary (Success / Failed / Errors)
# ─────────────────────────────────────────────

import re
import warnings

from templates.messages.topic_messages import MANAGE_TOPICS_HEADER

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"telegram\.ext\._conversationhandler",
)

from config.logger import log
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    CommandHandler,
)

from models.topics import create_topic_entry
from utils.auto_delete import schedule_delete
from utils.url_validator import parse_url
from templates.inlinebutton import (
    CANCEL_KEYBOARD,
    MANAGE_TOPICS_KEYBOARD,
)

# ── States ────────────────────────────────────
(
    STATE_TOPIC_KEY,
    STATE_TOPIC_URLS,
) = range(2)

_KEY_MSG_ID = "au_msg_id"
_KEY_CHAT_ID = "au_chat_id"


def _KEYBOARD_URL_KEYS() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_topic")],
        ]
    )


async def _edit_bubble(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard=None,
) -> None:
    chat_id = context.user_data.get(_KEY_CHAT_ID)
    msg_id = context.user_data.get(_KEY_MSG_ID)
    if not chat_id or not msg_id:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        schedule_delete(context, chat_id, msg_id)
        return
    except Exception as exc:
        err = str(exc)
        if "is not modified" in err:
            return
        log.debug("_edit_bubble silenced: %s", exc)
        try:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            context.user_data[_KEY_MSG_ID] = sent.message_id
            context.user_data[_KEY_CHAT_ID] = chat_id
            schedule_delete(context, chat_id, sent.message_id)
        except Exception:
            pass


def _auto_delete_user_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        schedule_delete(context, update.message.chat_id, update.message.message_id)


def _cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (_KEY_MSG_ID, _KEY_CHAT_ID, "nt_name", "nt_key"):
        context.user_data.pop(key, None)


async def cb_add_topic_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_topics"):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()

    context.user_data[_KEY_MSG_ID] = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id
    context.user_data.pop("nt_name", None)
    context.user_data.pop("nt_key", None)

    await _edit_bubble(
        context,
        "📋 <b>Add Topic — Step 1 / 3</b>\n\n"
        "🔑 Enter a <b>Name</b>\n"
        "(e.g. <code>Telegram</code> <code>Instagram</code> <code>Facebook</code>):",
        _KEYBOARD_URL_KEYS(),
    )
    return STATE_TOPIC_KEY


async def cb_set_url_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split(":")[1]
    name = key.capitalize()

    context.user_data["nt_key"] = key
    context.user_data["nt_name"] = name

    await _edit_bubble(
        context,
        f"📋 <b>Add Topic — Step 2 / 3</b>\n\n"
        f"📛 Name: <b>{name}</b>\n\n"
        "🔗 Enter Telegram <b>Destination</b> link(s) <b>line-by-line</b>:\n"
        "(Optional: specify <code> - interval</code> in seconds per line)\n\n"
        "<code>https://t.me/groups1 - 60</code>\n"
        "<code>https://t.me/groups2 - 120</code>",
        CANCEL_KEYBOARD(),
    )
    return STATE_TOPIC_URLS


async def recv_topic_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    raw_text = update.message.text.strip()
    if not raw_text:
        await _edit_bubble(
            context,
            "⚠️ Topic key cannot be empty.\n\nEnter Topic Key / Name:",
            CANCEL_KEYBOARD(),
        )
        return STATE_TOPIC_KEY

    name = raw_text
    key = raw_text.lower().replace(" ", "_")

    context.user_data["nt_name"] = name
    context.user_data["nt_key"] = key

    await _edit_bubble(
        context,
        f"📋 <b>Add Topic — Step 2 / 3</b>\n\n"
        f"📛 Name: <b>{name}</b>\n\n"
        "🔗 Enter Telegram <b>Destination</b> link(s) <b>line-by-line</b>:\n"
        "(Optional: specify <code> - interval</code> in seconds per line)\n\n"
        "<code>https://t.me/groups1 - 60</code>\n"
        "<code>https://t.me/groups2 - 120</code>",
        CANCEL_KEYBOARD(),
    )
    return STATE_TOPIC_URLS


async def recv_topic_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    text = update.message.text.strip()
    raw_lines = [line.strip() for line in text.split("\n") if line.strip()]

    if not raw_lines:
        await _edit_bubble(
            context,
            "⚠️ Enter at least one Telegram destination URL:",
            CANCEL_KEYBOARD(),
        )
        return STATE_TOPIC_URLS

    name = context.user_data.get("nt_name", "Topic")
    key = context.user_data.get("nt_key", "default")

    created_entries = []
    failed_entries = []

    for line in raw_lines:
        m = re.match(r"^(.*?)\s*[-:]\s*(\d+)\s*$", line)
        if m and m.group(1).strip():
            raw_url = m.group(1).strip()
            interval = int(m.group(2))
        else:
            raw_url = line
            interval = 60

        parsed = parse_url(raw_url)
        if parsed.get("type") == "invalid":
            failed_entries.append(f"❌ <code>{raw_url}</code>: {parsed.get('error')}")
            continue

        try:
            entry = create_topic_entry(
                url_key=key,
                name=name,
                url=parsed.get("normalized") or raw_url,
                enabled=True,
                interval_seconds=interval,
            )
            created_entries.append(entry)
        except Exception as exc:
            failed_entries.append(f"❌ <code>{raw_url}</code>: {exc}")

    # Step 3: Summary (Success | Failed | Error)
    success_count = len(created_entries)
    failed_count = len(failed_entries)

    summary_lines = [
        "📋 <b>Add Topic — Step 3 / 3 (Summary)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📛 <b>Name :</b> <b>{name}</b>\n\n"
        f"✅ <b>Success :</b>  <code>{success_count}</code> URL(s)\n"
        f"❌ <b>Failed :</b>   <code>{failed_count}</code> URL(s)\n"
    ]

    if failed_entries:
        summary_lines.append("⚠️ <b>Errors:</b>\n" + "\n".join(failed_entries[:5]))

    await _edit_bubble(
        context,
        "\n".join(summary_lines),
        MANAGE_TOPICS_KEYBOARD(),
    )

    _cleanup(context)
    return ConversationHandler.END


async def _safe_answer(
    query, text: str | None = None, show_alert: bool = False
) -> None:
    if not query:
        return
    try:
        if text:
            await query.answer(text, show_alert=show_alert)
        else:
            await query.answer()
    except Exception:
        pass


async def cb_topic_cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await _safe_answer(query, "Cancelled.")
    _cleanup(context)
    from handlers.topics.view_topic import cb_manage_topics

    await cb_manage_topics(update, context)
    return ConversationHandler.END


async def cmd_cancel_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    _cleanup(context)
    await _edit_bubble(context, MANAGE_TOPICS_HEADER, MANAGE_TOPICS_KEYBOARD())
    return ConversationHandler.END


def build_add_topic_conv() -> ConversationHandler:
    cancel_cb = CallbackQueryHandler(
        cb_topic_cancel_button, pattern=r"^(cancel|cancel_.*|global_cancel)$"
    )
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_add_topic_start, pattern=r"^add_topic$"),
        ],
        states={
            STATE_TOPIC_KEY: [
                cancel_cb,
                CallbackQueryHandler(
                    cb_set_url_key, pattern=r"^set_ukey:[A-Za-z0-9_]+$"
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_topic_key),
            ],
            STATE_TOPIC_URLS: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_topic_urls),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel_topic),
            cancel_cb,
        ],
        allow_reentry=True,
    )
