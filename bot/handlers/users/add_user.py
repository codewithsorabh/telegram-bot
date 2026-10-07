# ─────────────────────────────────────────────
#  handlers / users / add_user.py
#
#  ConversationHandler – Add User flow.
#  Edit-in-place bubble through all steps.
#
#  Steps:
#    1. API ID
#    2. API Hash
#    3. Phone number → OTP sent via Telethon
#    4. OTP code
#    5. 2FA password (if enabled, else skip)
#    6. Select topic / Custom topic / Skip
#    7. Select plan / Custom plan / Skip (default 30d)
#    8. Save to DB → success screen
# ─────────────────────────────────────────────

import asyncio
import warnings

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"telegram\.ext\._conversationhandler",
)

from config.logger import LOG, log
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
)
from telethon.sessions import StringSession

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    CommandHandler,
)

from models.users import save_telegram_user
from models.topics import get_all_topics
from models.plans import get_all_plans, create_plan
from utils.auto_delete import schedule_delete
from templates.messages import (
    ASK_AUTH,
    ASK_OTP,
    ASK_2FA,
    AUTH_SUCCESS_MSG,
    AUTH_FAILED,
    INVALID_API_ID,
    CANCELLED_MSG,
)
from templates.inlinebutton import (
    CANCEL_KEYBOARD,
    AUTH_SUCCESS_KEYBOARD,
    CANCELLED_KEYBOARD_USER,
)

# ── Conversation states ───────────────────────
(
    STATE_API_ID,
    STATE_API_HASH,
    STATE_PHONE,
    STATE_OTP,
    STATE_2FA,
    STATE_SELECT_TOPIC,
    STATE_CUSTOM_TOPIC_NAME,
    STATE_CUSTOM_TOPIC_URLS,
    STATE_SELECT_PLAN,
    STATE_CUSTOM_PLAN_DETAILS,
) = range(10)

# ── Context keys ──────────────────────────────
_KEY_API_ID = "au_api_id"
_KEY_API_HASH = "au_api_hash"
_KEY_PHONE = "au_phone"
_KEY_CLIENT = "au_client"
_KEY_PHONE_HASH = "au_phone_hash"
_KEY_MSG_ID = "au_msg_id"
_KEY_CHAT_ID = "au_chat_id"
_KEY_TEMP_SESS = "au_temp_sess"
_KEY_TOPIC_ID = "au_topic_id"


# ─────────────────────────────────────────────
#  Helper: edit bubble in-place
# ─────────────────────────────────────────────
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
        log.warning("_edit_bubble error (chat=%s msg=%s): %s", chat_id, msg_id, exc)
        try:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            context.user_data[_KEY_MSG_ID] = sent.message_id
            schedule_delete(context, chat_id, sent.message_id)
        except Exception:
            pass


def _auto_delete_user_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        schedule_delete(context, update.message.chat_id, update.message.message_id)


async def _cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    client: TelegramClient | None = context.user_data.pop(_KEY_CLIENT, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
    for key in (
        _KEY_API_ID,
        _KEY_API_HASH,
        _KEY_PHONE,
        _KEY_PHONE_HASH,
        _KEY_MSG_ID,
        _KEY_CHAT_ID,
        _KEY_TEMP_SESS,
        _KEY_TOPIC_ID,
        "new_topic_name",
        "au_reauth_uid",
        "otp_attempts",
    ):
        context.user_data.pop(key, None)


# ─────────────────────────────────────────────
#  Step 0 – Entry: tap ➕ Add User
# ─────────────────────────────────────────────
async def cb_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_users"):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()

    context.user_data[_KEY_MSG_ID] = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    await _edit_bubble(context, ASK_AUTH, CANCEL_KEYBOARD())
    return STATE_API_ID


async def cb_reauth_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start direct OTP re-authentication for an existing user account."""
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_users"):
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()

    try:
        uid = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Invalid account ID.", show_alert=True)
        return ConversationHandler.END

    from models.users import get_user_by_id

    user = get_user_by_id(uid)
    if not user:
        await query.answer("User account not found.", show_alert=True)
        return ConversationHandler.END

    api_id = user.get("api_id")
    api_hash = user.get("api_hash")
    phone = user.get("phone_number") or user.get("phone")

    if not api_id or not api_hash or not phone:
        await query.answer("Incomplete credentials for account.", show_alert=True)
        return ConversationHandler.END

    context.user_data[_KEY_MSG_ID] = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id
    context.user_data["au_reauth_uid"] = uid
    context.user_data[_KEY_API_ID] = api_id
    context.user_data[_KEY_API_HASH] = api_hash
    context.user_data[_KEY_PHONE] = phone

    await _edit_bubble(
        context,
        f"📡 <b>Sending OTP for {phone}…</b>\n\nConnecting to Telegram, please wait.",
        CANCEL_KEYBOARD(),
    )

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    try:
        await client.connect()
    except Exception as exc:
        log.error("reauth client.connect() failed: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ <b>Failed to connect to Telegram.</b>\n\n<code>{exc}</code>\n\nNo changes saved.",
            CANCELLED_KEYBOARD_USER(),
        )
        await _cleanup(context)
        return ConversationHandler.END

    try:
        result = await client.send_code_request(phone)
        context.user_data[_KEY_PHONE_HASH] = result.phone_code_hash
        context.user_data[_KEY_CLIENT] = client
    except Exception as exc:
        log.error("send_code_request re-auth failed: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ <b>Could not send OTP for Re-Auth</b>\n\n<code>{exc}</code>\n\nNo changes saved.",
            CANCELLED_KEYBOARD_USER(),
        )
        await client.disconnect()
        await _cleanup(context)
        return ConversationHandler.END

    await _edit_bubble(
        context,
        ASK_OTP(phone),
        CANCEL_KEYBOARD(),
    )
    return STATE_OTP


async def recv_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse API ID, API Hash, and Phone from a single multi-line message."""
    _auto_delete_user_msg(update, context)
    lines = [l.strip() for l in update.message.text.strip().splitlines() if l.strip()]

    if len(lines) < 3:
        await _edit_bubble(
            context,
            "⚠️ <b>Invalid format.</b> Please enter all 3 lines:\n\n"
            "<code>API_ID</code>\n<code>API_HASH</code>\n<code>+PHONE_NUMBER</code>",
            CANCEL_KEYBOARD(),
        )
        return STATE_API_ID

    api_id_str, api_hash, phone = lines[0], lines[1], lines[2]

    if not api_id_str.isdigit():
        await _edit_bubble(
            context,
            INVALID_API_ID + "\n\nThe first line must be a numeric API ID.",
            CANCEL_KEYBOARD(),
        )
        return STATE_API_ID

    context.user_data[_KEY_API_ID] = int(api_id_str)
    context.user_data[_KEY_API_HASH] = api_hash
    context.user_data[_KEY_PHONE] = phone

    await _edit_bubble(
        context,
        "📡 <b>Sending OTP…</b>\n\nConnecting to Telegram, please wait.",
        CANCEL_KEYBOARD(),
    )

    client = TelegramClient(StringSession(), int(api_id_str), api_hash)
    try:
        await client.connect()
    except Exception as exc:
        log.error("client.connect() failed: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ <b>Failed to connect to Telegram.</b>\n\n<code>{exc}</code>\n\nNo data was saved.",
            CANCELLED_KEYBOARD_USER(),
        )
        await _cleanup(context)
        return ConversationHandler.END

    try:
        result = await client.send_code_request(phone)
        context.user_data[_KEY_PHONE_HASH] = result.phone_code_hash
        context.user_data[_KEY_CLIENT] = client
    except Exception as exc:
        log.error("send_code_request failed: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ <b>Could not send OTP</b>\n\n<code>{exc}</code>\n\nNo data was saved.",
            CANCELLED_KEYBOARD_USER(),
        )
        await client.disconnect()
        await _cleanup(context)
        return ConversationHandler.END

    await _edit_bubble(context, ASK_OTP(phone), CANCEL_KEYBOARD())
    return STATE_OTP


async def recv_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    raw_text = (
        update.message.text.strip() if update.message and update.message.text else ""
    )
    otp = "".join(c for c in raw_text if c.isdigit())

    phone = context.user_data.get(_KEY_PHONE, "")
    phone_code_hash = context.user_data.get(_KEY_PHONE_HASH, "")
    client: TelegramClient | None = context.user_data.get(_KEY_CLIENT)

    if not client or not client.is_connected():
        await _edit_bubble(
            context,
            "⚠️ <b>Session connection lost.</b>\n\nPlease restart to request a new OTP code.",
            CANCELLED_KEYBOARD_USER(),
        )
        await _cleanup(context)
        return ConversationHandler.END

    if not otp:
        await _edit_bubble(
            context,
            "⚠️ <b>Invalid OTP format.</b> Please enter only the numeric code (e.g. <code>123456</code>):",
            CANCEL_KEYBOARD(),
        )
        return STATE_OTP

    await _edit_bubble(
        context,
        "⏳ <b>Verifying OTP Code…</b>\n\nAuthenticating with Telegram servers, please wait.",
        CANCEL_KEYBOARD(),
    )

    try:
        await asyncio.wait_for(
            client.sign_in(phone, otp, phone_code_hash=phone_code_hash),
            timeout=30.0,
        )
        return await _finish_auth(update, context, client)

    except asyncio.TimeoutError:
        log.error("sign_in timed out after 30s for phone %s", phone)
        await _edit_bubble(
            context,
            "⚠️ <b>OTP verification timed out (30s).</b>\n\nTelegram server took too long to respond. Please check your network or try again.",
            CANCEL_KEYBOARD(),
        )
        return STATE_OTP

    except SessionPasswordNeededError:
        await _edit_bubble(context, ASK_2FA, CANCEL_KEYBOARD())
        return STATE_2FA

    except PhoneCodeInvalidError:
        attempts = context.user_data.get("otp_attempts", 0) + 1
        context.user_data["otp_attempts"] = attempts
        if attempts < 3:
            await _edit_bubble(
                context,
                f"⚠️ <b>Incorrect OTP code (Attempt {attempts}/3).</b>\n\n"
                f"Please check the code sent to your Telegram app / SMS for <b>{phone}</b> and try again:",
                CANCEL_KEYBOARD(),
            )
            return STATE_OTP
        else:
            await _edit_bubble(
                context,
                "❌ <b>Too many incorrect OTP attempts.</b>\n\nNo data was saved.",
                CANCELLED_KEYBOARD_USER(),
            )
            await client.disconnect()
            await _cleanup(context)
            return ConversationHandler.END

    except PhoneCodeExpiredError:
        phone_code_hash = context.user_data.get(_KEY_PHONE_HASH)
        resend_btn = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔄 Resend OTP Code", callback_data="resend_otp_code"
                    )
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_user")],
            ]
        )
        await _edit_bubble(
            context,
            "⏳ <b>OTP Confirmation Code Expired.</b>\n\n"
            "The code has expired on Telegram's servers.\n"
            "Click <b>🔄 Resend OTP Code</b> below to send a brand new code to your Telegram app / SMS.",
            resend_btn,
        )
        return STATE_OTP

    except Exception as exc:
        # Catch-all: FloodWaitError, dropped connection, RPC errors, etc.
        # Without this, sign_in() failures left the bubble stuck on
        # "Verifying OTP Code…" forever — no success, fail, or error shown.
        log.error("recv_otp sign_in unexpected error: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ <b>OTP verification failed.</b>\n\n<code>{exc}</code>\n\nNo data was saved.",
            CANCELLED_KEYBOARD_USER(),
        )
        try:
            await client.disconnect()
        except Exception:
            pass
        await _cleanup(context)
        return ConversationHandler.END


async def cb_resend_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resend a fresh OTP code when user clicks 🔄 Resend OTP Code."""
    query = update.callback_query
    await query.answer("Requesting new OTP code...")

    phone = context.user_data.get(_KEY_PHONE)
    client: TelegramClient | None = context.user_data.get(_KEY_CLIENT)

    if not client or not phone:
        await _edit_bubble(
            context,
            "⚠️ <b>Session lost.</b> Please click ➕ Add User to restart.",
            CANCELLED_KEYBOARD_USER(),
        )
        await _cleanup(context)
        return ConversationHandler.END

    try:
        if not client.is_connected():
            await client.connect()
        result = await client.send_code_request(phone)
        context.user_data[_KEY_PHONE_HASH] = result.phone_code_hash
        context.user_data["otp_attempts"] = 0

        await _edit_bubble(
            context,
            f"📩 <b>New OTP Sent!</b>\n\nA fresh confirmation code was sent to <b>{phone}</b> via Telegram/SMS.\n\n"
            + ASK_OTP(phone),
            CANCEL_KEYBOARD(),
        )
        return STATE_OTP
    except Exception as exc:
        log.error("resend_otp failed: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ <b>Could not resend OTP</b>\n\n<code>{exc}</code>\n\nPlease try again later.",
            CANCELLED_KEYBOARD_USER(),
        )
        await client.disconnect()
        await _cleanup(context)
        return ConversationHandler.END


async def recv_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    password = update.message.text.strip()
    client: TelegramClient = context.user_data[_KEY_CLIENT]

    try:
        await client.sign_in(password=password)
        return await _finish_auth(update, context, client)

    except PasswordHashInvalidError:
        await _edit_bubble(
            context,
            "❌ <b>Wrong 2FA password.</b> Please try again.\n\n" + ASK_2FA,
            CANCEL_KEYBOARD(),
        )
        return STATE_2FA

    except Exception as exc:
        log.error("sign_in 2FA error: %s", exc)
        await _edit_bubble(
            context,
            AUTH_FAILED + "\n\nNo data was saved.",
            CANCELLED_KEYBOARD_USER(),
        )
        await client.disconnect()
        await _cleanup(context)
        return ConversationHandler.END


async def _finish_auth(update, context, client: TelegramClient):
    try:
        session_string = client.session.save()
        await client.disconnect()
        context.user_data[_KEY_TEMP_SESS] = session_string
    except Exception as exc:
        log.error("_finish_auth session save/disconnect error: %s", exc)

    reauth_uid = context.user_data.get("au_reauth_uid")
    if reauth_uid:
        try:
            from models.users import update_session_string

            update_session_string(reauth_uid, context.user_data[_KEY_TEMP_SESS])
        except Exception as exc:
            log.error("_finish_auth reauth session update error: %s", exc)
        phone = context.user_data.get(_KEY_PHONE, "Account")
        await _edit_bubble(
            context,
            f"✅ <b>Session Re-Authenticated!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 <code>{phone}</code> session renewed and stored securely. 🎉",
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Back to Account",
                            callback_data=f"user_detail:{reauth_uid}:all",
                        )
                    ]
                ]
            ),
        )
        await _cleanup(context)
        return ConversationHandler.END

    try:
        from models.config import is_topic_step_enabled

        if not is_topic_step_enabled():
            context.user_data[_KEY_TOPIC_ID] = None
            return await _save_and_end(update, context)

        from models.topics import get_topic_keys_summary

        topics_summary = get_topic_keys_summary()

        if not topics_summary:
            context.user_data[_KEY_TOPIC_ID] = None
            return await _save_and_end(update, context)

        buttons = []
        for t in topics_summary[:15]:
            label = f"📋 {t['name']} ({t['url_key']}) — {t['active_urls']}/{t['total_urls']} URLs"
            buttons.append(
                [InlineKeyboardButton(label, callback_data=f"seltopic:{t['url_key']}")]
            )
        buttons.append(
            [InlineKeyboardButton("⏭️ Skip (No Topic)", callback_data="seltopic_skip")]
        )

        await _edit_bubble(
            context,
            "🔒 <b>Authentication Successful!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Select a topic to assign this account to:",
            InlineKeyboardMarkup(buttons),
        )
        return STATE_SELECT_TOPIC

    except Exception as exc:
        log.error("_finish_auth topic step error: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ <b>Login succeeded but an error occurred on the next step.</b>\n\n"
            f"<code>{exc}</code>\n\nNo data was saved. Please try adding the user again.",
            CANCELLED_KEYBOARD_USER(),
        )
        await _cleanup(context)
        return ConversationHandler.END


async def cb_select_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "seltopic_custom":
        await _edit_bubble(
            context,
            "📋 <b>Custom Topic Name</b>\n\nEnter topic name:",
            CANCEL_KEYBOARD(),
        )
        return STATE_CUSTOM_TOPIC_NAME

    if data == "seltopic_skip":
        context.user_data[_KEY_TOPIC_ID] = None
        return await _save_and_end(update, context)

    if data.startswith("seltopic:"):
        url_key = data.split(":", 1)[1]
        context.user_data[_KEY_TOPIC_ID] = url_key
        return await _save_and_end(update, context)


async def recv_custom_topic_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    name = update.message.text.strip()
    if not name:
        return STATE_CUSTOM_TOPIC_NAME

    context.user_data["new_topic_name"] = name
    await _edit_bubble(
        context,
        "📋 <b>Custom Topic URLs</b>\n\nEnter Telegram URLs line-by-line:",
        CANCEL_KEYBOARD(),
    )
    return STATE_CUSTOM_TOPIC_URLS


async def recv_custom_topic_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    text = update.message.text.strip()
    name = context.user_data.pop("new_topic_name", "Topic")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    from models.topics import create_topic_entry

    url_key = name.lower().replace(" ", "_")
    try:
        for url in lines:
            create_topic_entry(url_key=url_key, name=name, url=url)
        context.user_data[_KEY_TOPIC_ID] = url_key
        return await _save_and_end(update, context)
    except Exception as exc:
        log.error("Failed to create custom topic: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ <b>Failed to create custom topic:</b>\n<code>{exc}</code>",
            CANCELLED_KEYBOARD_USER(),
        )
        await _cleanup(context)
        return ConversationHandler.END


# ─────────────────────────────────────────────
#  Save user to DB
# ─────────────────────────────────────────────
async def _save_and_end(
    update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int | None = None
):
    bot_user_id = update.effective_user.id
    api_id = context.user_data[_KEY_API_ID]
    api_hash = context.user_data[_KEY_API_HASH]
    phone = context.user_data[_KEY_PHONE]
    session = context.user_data[_KEY_TEMP_SESS]
    topic_id = context.user_data.get(_KEY_TOPIC_ID)

    try:
        save_telegram_user(
            added_by=bot_user_id,
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            session_string=session,
            topic_id=topic_id,
            is_forward=False,
        )
        await _edit_bubble(context, AUTH_SUCCESS_MSG(phone), AUTH_SUCCESS_KEYBOARD())
    except Exception as exc:
        log.error("DB save error: %s", exc)
        await _edit_bubble(
            context,
            f"⚠️ Authenticated but <b>DB save failed</b>:\n<code>{exc}</code>",
            CANCELLED_KEYBOARD_USER(),
        )

    await _cleanup(context)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    await _cleanup(context)
    from handlers.users.view_user import cb_view_users
    await cb_view_users(update, context)
    return ConversationHandler.END


async def cb_cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the ❌ Cancel inline button inside the ConversationHandler."""
    query = update.callback_query
    if query:
        try:
            await query.answer("Cancelled.")
        except Exception:
            pass
    await _cleanup(context)
    from handlers.users.view_user import cb_view_users
    await cb_view_users(update, context)
    return ConversationHandler.END


def build_add_user_conv() -> ConversationHandler:
    cancel_cb = CallbackQueryHandler(cb_cancel_button, pattern=r"^(cancel|cancel_.*|global_cancel)$")
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_add_user, pattern=r"^add_user$"),
            CallbackQueryHandler(cb_reauth_start, pattern=r"^u_reauth:\d+$"),
        ],
        states={
            STATE_API_ID: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_credentials),
            ],
            STATE_OTP: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_otp),
                CallbackQueryHandler(cb_resend_otp, pattern=r"^resend_otp_code$"),
            ],
            STATE_2FA: [cancel_cb, MessageHandler(filters.TEXT & ~filters.COMMAND, recv_2fa)],
            STATE_SELECT_TOPIC: [
                cancel_cb,
                CallbackQueryHandler(cb_select_topic, pattern=r"^seltopic:"),
                CallbackQueryHandler(
                    cb_select_topic, pattern=r"^seltopic_(custom|skip)$"
                ),
            ],
            STATE_CUSTOM_TOPIC_NAME: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_custom_topic_name),
            ],
            STATE_CUSTOM_TOPIC_URLS: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_custom_topic_urls),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            cancel_cb,
        ],
        allow_reentry=True,
    )
