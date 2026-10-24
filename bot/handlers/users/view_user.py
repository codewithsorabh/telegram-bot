# ─────────────────────────────────────────────
#  handlers / users / view_user.py
#
#  User account details & interactive action controls:
#    • Live session validation
#    • Toggle Forwarding (▶️ Running / ⏸️ Stopped)
#    • Update Delay (seconds)
#    • Change Topic
#    • Upgrade Plan
#    • Re-Auth expired session
#    • Delete Account
# ─────────────────────────────────────────────

import warnings

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"telegram\.ext\._conversationhandler",
)

from config.logger import LOG, log
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    CommandHandler,
)

from models.users import (
    get_all_users,
    get_user_by_id,
    get_banned_users,
    update_user_forwarding,
    update_user_topic,
    update_user_plan,
    upgrade_user_plan,
    update_user_channel_id,
    delete_telegram_user,
)
from models.topics import get_all_topics, get_topic_keys_summary
from models.plans import get_all_plans
from utils.auto_delete import schedule_delete
from templates.messages import (
    WELCOME_MSG,
    VIEW_USERS_HEADER,
    VIEW_USERS_EMPTY,
    USER_DETAIL_MSG,
)
from templates.inlinebutton import (
    WELCOME_KEYBOARD,
    VIEW_USERS_KEYBOARD,
    USER_DETAIL_KEYBOARD,
    CANCEL_KEYBOARD,
)

# ── State constants ──────────────────────────────────────────
_KEY_MSG_ID = "au_msg_id"
_KEY_CHAT_ID = "au_chat_id"
_KEY_USER_ID = "vu_edit_uid"
STATE_EDIT_CHANNEL = 101


def _reset_bubble_timer(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
) -> None:
    schedule_delete(context, chat_id, message_id)


async def check_user_session_status(user: dict) -> str:
    """
    Validates session with Telethon StringSession.
    Returns status: 'active', 'expired', 'banned', etc.
    """
    api_id = user.get("api_id")
    api_hash = user.get("api_hash")
    session_string = user.get("session_string")
    phone = user.get("phone", "unknown")

    if not api_id or not api_hash or not session_string or len(session_string) < 50:
        status = "expired"
        LOG.debug(
            "Session validation for phone=%s -> status=%s (missing credentials or invalid session)",
            phone,
            status,
        )
        return status

    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import (
        UserDeactivatedError,
        UserDeactivatedBanError,
        AuthKeyUnregisteredError,
        SessionRevokedError,
        SessionExpiredError,
    )

    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
    except Exception as exc:
        LOG.error("Failed to parse StringSession for phone=%s: %s", phone, exc)
        return "expired"
    try:
        await client.connect()
        if not await client.is_user_authorized():
            status = "expired"
        else:
            status = "active"
    except (UserDeactivatedBanError, UserDeactivatedError) as exc:
        status = "banned"
        LOG.warning(
            "Session validation for phone=%s -> status=%s (%s)", phone, status, exc
        )
    except (AuthKeyUnregisteredError, SessionRevokedError, SessionExpiredError) as exc:
        status = "expired"
        LOG.warning(
            "Session validation for phone=%s -> status=%s (%s)", phone, status, exc
        )
    except Exception as exc:
        LOG.error("Session validation for phone=%s -> error: %s", phone, exc)
        status = "expired"
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    LOG.debug("Session validation for phone=%s -> status=%s", phone, status)
    return status


# ─────────────────────────────────────────────
#  👥 View Users list
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
#  👥 View Users list (Category Menu & Filtered Views)
# ─────────────────────────────────────────────
async def cb_view_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_users"):
        return

    query = update.callback_query
    await _safe_answer(query, "Loading accounts status...")

    users = get_all_users()
    banned = get_banned_users()
    banned_ids = {u["id"] for u in banned}

    expired_count = sum(1 for u in users if u.get("session_status") == "expired")
    banned_count = sum(1 for u in users if u.get("session_status") in ("banned", "suspended") or u["id"] in banned_ids)
    active_count = max(0, len(users) - expired_count - banned_count)

    counts = {
        "all": len(users),
        "expired": expired_count,
        "banned": banned_count,
        "active": active_count,
    }

    text = (
        "👥 <b>Linked User Accounts Management</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Total Accounts  : <b>{counts['all']}</b>\n"
        f"⚠️ Expired Sessions: <b>{counts['expired']}</b>\n"
        f"🚫 Banned Users    : <b>{counts['banned']}</b>\n\n"
        "Select a category below to view accounts:"
    )

    await _safe_edit_query_message(
        query,
        text=text,
        reply_markup=VIEW_USERS_KEYBOARD([], view_mode="main", counts=counts),
    )
    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)


async def _safe_edit_query_message(query, text: str, reply_markup=None) -> None:
    if not query:
        return
    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        err_msg = str(exc)
        if (
            "Message is not modified" in err_msg
            or "There is no text in the message to edit" in err_msg
        ):
            return
        log.warning("_safe_edit_query_message BadRequest: %s", exc)
    except Exception as exc:
        log.warning("_safe_edit_query_message error: %s", exc)


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


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)


async def _reply_list_bubble(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None
) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    sent = await update.message.reply_html(
        text,
        reply_markup=reply_markup,
    )
    context.user_data[_KEY_MSG_ID] = sent.message_id
    context.user_data[_KEY_CHAT_ID] = sent.chat_id
    schedule_delete(context, sent.chat_id, sent.message_id)


async def _show_filtered_users(
    update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str = "all", page: int = 1
):
    query = update.callback_query

    all_u = get_all_users()
    if mode == "banned":
        users = get_banned_users()
        category_title = "🚫 Banned Accounts"
    elif mode == "expired":
        banned_ids = {u["id"] for u in get_banned_users()}
        users = [
            u for u in all_u
            if u.get("session_status") == "expired" and u["id"] not in banned_ids
        ]
        category_title = "⚠️ Expired Sessions"
    elif mode == "forward_true":
        users = [
            u for u in all_u
            if (u.get("forwarding_enabled") if u.get("forwarding_enabled") is not None else u.get("is_forward", True))
        ]
        category_title = "▶️ Active Forwarding Accounts"
    elif mode == "forward_false":
        users = [
            u
            for u in all_u
            if not (u.get("forwarding_enabled") if u.get("forwarding_enabled") is not None else u.get("is_forward", True))
        ]
        category_title = "⏸️ Stopped Forwarding Accounts"
    elif mode == "topic_none":
        users = [u for u in all_u if not u.get("topic_id")]
        category_title = "📋 Accounts Without Topic"
    elif mode == "log_true":
        users = [u for u in all_u if u.get("channel_id")]
        category_title = "📣 Log Channel Configured Accounts"
    elif mode == "log_false":
        users = [u for u in all_u if not u.get("channel_id")]
        category_title = "📣 Accounts Without Log Channel"
    else:
        users = all_u
        category_title = "📱 All Accounts"

    from models.config import get_page_size

    page_size = get_page_size()
    from utils.pagination import paginate

    p_data = paginate(users, page=page, page_size=page_size)

    if not users:
        msg = f"<b>{category_title} (0)</b>\n━━━━━━━━━━━━━━━━━━━━━\n<i>No accounts found in this category.</i>"
    else:
        header = f"<b>{category_title} ({p_data['total_items']})</b>"
        if p_data["total_pages"] > 1:
            header += f" — Page {p_data['page']}/{p_data['total_pages']} ({p_data['start_idx']}-{p_data['end_idx']})"
        msg = f"{header}\n━━━━━━━━━━━━━━━━━━━━━\nSelect an account to view or manage:"

    if query:
        await _safe_edit_query_message(
            query,
            text=msg,
            reply_markup=VIEW_USERS_KEYBOARD(
                users, view_mode=mode, page=p_data["page"], page_size=page_size
            ),
        )
        if query.message:
            _reset_bubble_timer(
                context, query.message.chat_id, query.message.message_id
            )
    else:
        await _reply_list_bubble(
            update,
            context,
            text=msg,
            reply_markup=VIEW_USERS_KEYBOARD(
                users, view_mode=mode, page=p_data["page"], page_size=page_size
            ),
        )


async def cb_view_users_filtered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query, "Loading accounts list...")

    data = query.data or "view_users_all"
    page = 1
    if ":page:" in data:
        parts = data.split(":page:")
        mode = parts[0].replace("view_users_", "")
        try:
            page = int(parts[1])
        except ValueError:
            page = 1
    else:
        mode = data.replace("view_users_", "")

    await _show_filtered_users(update, context, mode=mode, page=page)


# ─────────────────────────────────────────────
#  📋 User Detail Card
# ─────────────────────────────────────────────
async def cb_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Checking account status...")

    parts = query.data.split(":")
    user_id = int(parts[1])
    view_mode = parts[2] if len(parts) > 2 else "all"
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

    user = get_user_by_id(user_id)
    if not user:
        await query.answer("User not found.", show_alert=True)
        return

    validate_status = user.get("session_status", "active")
    user["validate"] = validate_status

    await _safe_edit_query_message(
        query,
        text=USER_DETAIL_MSG(user),
        reply_markup=USER_DETAIL_KEYBOARD(user, view_mode=view_mode, page=page),
    )
    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)


# ─────────────────────────────────────────────
#  🔑 Re-Auth Prompt
# ─────────────────────────────────────────────
async def cb_reauth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    uid = int(parts[1])

    user = get_user_by_id(uid)
    phone = user.get("phone_number") if user else "Unknown"

    text = (
        f"🔑 <b>Re-Authenticate Account</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Account: <code>{phone}</code> (DB ID: {uid})\n\n"
        f"To re-authenticate this account, please click <b>➕ Add New User Account</b> "
        f"and submit new credentials line-by-line for <code>{phone}</code>.\n\n"
        f"It will update the existing account session seamlessly."
    )

    buttons = [
        [InlineKeyboardButton("➕ Add / Re-Auth Account", callback_data="add_user")],
        [
            InlineKeyboardButton(
                "⬅️ Back to Account", callback_data=f"user_detail:{uid}:expired"
            )
        ],
    ]
    await _safe_edit_query_message(
        query,
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)


# ─────────────────────────────────────────────
#  ▶️ Toggle Forwarding (Running / Stopped)
# ─────────────────────────────────────────────
async def cb_toggle_fwd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    uid = int(parts[1])
    new_val = bool(int(parts[2]))
    view_mode = parts[3] if len(parts) > 3 else "all"

    user = update_user_forwarding(uid, new_val)
    if user:
        # Signal scheduler to start/stop worker immediately
        scheduler = context.bot_data.get("scheduler")
        if scheduler:
            if new_val:
                scheduler.signal_start(uid)
            else:
                scheduler.signal_stop(uid)

        # Broadcast signal to separate userbot process via Redis
        try:
            import json
            from config.db import r
            if r:
                r.publish(
                    "userbot:signals",
                    json.dumps({"action": "start" if new_val else "stop", "user_id": uid}),
                )
        except Exception:
            pass

        status_txt = "▶️ Forwarding Started" if new_val else "⏸️ Forwarding Stopped"
        await query.answer(status_txt, show_alert=True)
        await _safe_edit_query_message(
            query,
            text=USER_DETAIL_MSG(user),
            reply_markup=USER_DETAIL_KEYBOARD(user, view_mode=view_mode),
        )
    else:
        await query.answer("⚠️ User not found.", show_alert=True)
    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)


# ─────────────────────────────────────────────
#  📋 Change Topic
# ─────────────────────────────────────────────
async def cb_change_topic_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split(":")[1])

    # Show distinct url_key groups (one button per topic group)
    topics_summary = get_topic_keys_summary()
    buttons = []
    for t in topics_summary:
        label = f"📋 {t['name']} ({t['url_key']}) — {t['active_urls']}/{t['total_urls']} URLs"
        buttons.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"u_set_topic:{uid}:{t['url_key']}"
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton("❌ Remove Topic", callback_data=f"u_set_topic:{uid}:")]
    )
    buttons.append(
        [InlineKeyboardButton("⬅️ Back to Account", callback_data=f"user_detail:{uid}")]
    )

    await _safe_edit_query_message(
        query,
        text="📋 <b>Select New Topic</b> for this account:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)


async def cb_set_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    uid = int(parts[1])
    # parts[2] is url_key string or empty string (remove)
    url_key = parts[2] if len(parts) > 2 else ""
    topic_val = url_key if url_key else None

    user = update_user_topic(uid, topic_val)
    await query.answer("Topic updated!", show_alert=True)

    if user:
        user["validate"] = user.get("session_status", "active")
        await _safe_edit_query_message(
            query,
            text=USER_DETAIL_MSG(user),
            reply_markup=USER_DETAIL_KEYBOARD(user, view_mode="all"),
        )
    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)


# ─────────────────────────────────────────────
#  🗑️ Delete Account
# ─────────────────────────────────────────────
async def cb_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    uid = int(parts[1])
    view_mode = parts[2] if len(parts) > 2 else "all"

    buttons = [
        [
            InlineKeyboardButton(
                "✅ Yes, Delete", callback_data=f"u_delete_do:{uid}:{view_mode}"
            ),
            InlineKeyboardButton(
                "❌ Cancel", callback_data=f"user_detail:{uid}:{view_mode}"
            ),
        ]
    ]
    await _safe_edit_query_message(
        query,
        text="⚠️ <b>Are you sure you want to delete this linked account?</b>\n\nThis action cannot be undone.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)


async def cb_delete_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    uid = int(parts[1])
    view_mode = parts[2] if len(parts) > 2 else "all"

    delete_telegram_user(uid)
    await query.answer("✅ Account deleted successfully!", show_alert=True)

    # Return cleanly to filtered users list
    await _show_filtered_users(update, context, mode=view_mode, page=1)


async def cb_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    await _safe_edit_query_message(
        query,
        text=WELCOME_MSG(user),
        reply_markup=WELCOME_KEYBOARD(user.id if user else 0),
    )

    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)


async def cb_change_channel_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split(":")[1])
    context.user_data[_KEY_USER_ID] = uid

    user_obj = get_user_by_id(uid)
    curr_ch = user_obj.get("channel_id") if user_obj else None

    msg = (
        f"📣 <b>Set Log Channel ID</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Current: <code>{curr_ch or 'Not set'}</code>\n\n"
        f"Enter Telegram Channel ID (e.g. <code>-1001234567890</code> or <code>0</code> to clear):"
    )
    await _safe_edit_query_message(
        query,
        text=msg,
        reply_markup=CANCEL_KEYBOARD(),
    )
    if query and query.message:
        _reset_bubble_timer(context, query.message.chat_id, query.message.message_id)
    return STATE_EDIT_CHANNEL


async def recv_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get(_KEY_USER_ID)
    schedule_delete(context, update.message.chat_id, update.message.message_id)

    ch_id = None
    if text != "0":
        clean = text.lstrip("-")
        if not clean.isdigit():
            sent = await update.message.reply_html(
                "⚠️ Invalid Channel ID. Must be numbers (e.g. <code>-1001234567890</code> or <code>0</code> to clear):",
                reply_markup=CANCEL_KEYBOARD(),
            )
            schedule_delete(context, sent.chat_id, sent.message_id)
            return STATE_EDIT_CHANNEL
        ch_id = int(text)

    if uid:
        update_user_channel_id(uid, ch_id)

    # Return to user details
    u = get_user_by_id(uid) if uid else None
    if u:
        sent = await update.message.reply_html(
            USER_DETAIL_MSG(u),
            reply_markup=USER_DETAIL_KEYBOARD(u),
        )
        schedule_delete(context, sent.chat_id, sent.message_id)
    return ConversationHandler.END


async def cb_user_edit_cancel_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    if query:
        await _safe_answer(query, "Cancelled.")
    await cb_view_users(update, context)
    return ConversationHandler.END


def build_user_edit_channel_conv() -> ConversationHandler:
    cancel_cb = CallbackQueryHandler(
        cb_user_edit_cancel_button, pattern=r"^(cancel|cancel_.*|global_cancel)$"
    )
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                cb_change_channel_prompt, pattern=r"^u_change_channel:\d+$"
            ),
        ],
        states={
            STATE_EDIT_CHANNEL: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_channel_id),
            ],
        },
        fallbacks=[cancel_cb],
        allow_reentry=True,
    )


def build_user_edit_delay_conv() -> ConversationHandler:
    """Stub: delay editing removed — interval is now managed per-topic."""
    from telegram.ext import ConversationHandler

    return ConversationHandler(
        entry_points=[],
        states={},
        fallbacks=[],
        allow_reentry=True,
    )


def build_user_edit_source_conv() -> ConversationHandler:
    """Stub: source editing removed — source_url is now managed per-topic."""
    from telegram.ext import ConversationHandler

    return ConversationHandler(
        entry_points=[],
        states={},
        fallbacks=[],
        allow_reentry=True,
    )


# ─────────────────────────────────────────────
#  🔍 /find <phone_number> Command
# ─────────────────────────────────────────────
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_users"):
        return
    schedule_delete(context, update.message.chat_id, update.message.message_id)

    if not context.args:
        sent = await update.message.reply_html(
            "🔍 <b>Usage:</b> <code>/find &lt;phone_number&gt;</code>\n\n"
            "Example: <code>/find 9630733973</code> \n<code>/find +919630733973</code>"
        )
        schedule_delete(context, sent.chat_id, sent.message_id)
        return

    query_str = "".join(context.args).strip()
    clean_digits = "".join(c for c in query_str if c.isdigit())

    all_users = get_all_users()
    matches = []
    for u in all_users:
        phone = u.get("phone_number") or ""
        p_digits = "".join(c for c in phone if c.isdigit())
        if query_str.lower() in phone.lower() or (
            clean_digits and clean_digits in p_digits
        ):
            matches.append(u)

    if not matches:
        sent = await update.message.reply_html(
            f"❌ <b>No linked account found matching:</b> <code>{query_str}</code>"
        )
        schedule_delete(context, sent.chat_id, sent.message_id)
        return

    if len(matches) == 1:
        target_u = matches[0]
        target_u["validate"] = target_u.get("session_status", "active")
        sent = await update.message.reply_html(
            USER_DETAIL_MSG(target_u),
            reply_markup=USER_DETAIL_KEYBOARD(target_u, view_mode="all"),
        )
        schedule_delete(context, sent.chat_id, sent.message_id)
    else:
        from models.config import get_page_size

        page_size = get_page_size()
        sent = await update.message.reply_html(
            f"🔍 <b>Found {len(matches)} matching accounts for:</b> <code>{query_str}</code>\n━━━━━━━━━━━━━━━━━━━━━\nSelect an account below:",
            reply_markup=VIEW_USERS_KEYBOARD(
                matches, view_mode="all", page=1, page_size=page_size
            ),
        )
        schedule_delete(context, sent.chat_id, sent.message_id)


# ─────────────────────────────────────────────
#  🆔 /id <account_id> Command
# ─────────────────────────────────────────────
async def cmd_user_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_users"):
        return
    schedule_delete(context, update.message.chat_id, update.message.message_id)

    if not context.args or not context.args[0].isdigit():
        sent = await update.message.reply_html(
            "🆔 <b>Usage:</b> <code>/id &lt;account_id&gt;</code>\n"
            "Example: <code>/id 1</code> to view acc with ID 1."
        )
        schedule_delete(context, sent.chat_id, sent.message_id)
        return

    account_id = int(context.args[0])
    user_obj = get_user_by_id(account_id)

    if not user_obj:
        sent = await update.message.reply_html(
            f"❌ <b>Account ID</b> <code>{account_id}</code> not found."
        )
        schedule_delete(context, sent.chat_id, sent.message_id)
        return

    user_obj["validate"] = user_obj.get("session_status", "active")
    sent = await update.message.reply_html(
        USER_DETAIL_MSG(user_obj),
        reply_markup=USER_DETAIL_KEYBOARD(user_obj, view_mode="all"),
    )
    schedule_delete(context, sent.chat_id, sent.message_id)


# ─────────────────────────────────────────────
#  🔄 /forward true|false Command
# ─────────────────────────────────────────────
async def cmd_forward_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_users"):
        return
    schedule_delete(context, update.message.chat_id, update.message.message_id)

    if not context.args:
        sent = await update.message.reply_html(
            "🔄 <b>Usage:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "• <code>/forward true</code> — forwarding ON\n"
            "• <code>/forward false</code> — forwarding OFF"
        )
        schedule_delete(context, sent.chat_id, sent.message_id)
        return

    arg = context.args[0].strip().lower()
    if arg in ("true", "1", "on"):
        await _show_filtered_users(update, context, mode="forward_true", page=1)
    elif arg in ("false", "0", "off"):
        await _show_filtered_users(update, context, mode="forward_false", page=1)
    else:
        sent = await update.message.reply_html(
            "⚠️ Invalid argument. Please use <code>/forward true</code> or <code>/forward false</code>."
        )
        schedule_delete(context, sent.chat_id, sent.message_id)


# ─────────────────────────────────────────────
#  📋 /topic Command
# ─────────────────────────────────────────────
async def cmd_topic_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_users"):
        return
    schedule_delete(context, update.message.chat_id, update.message.message_id)

    await _show_filtered_users(update, context, mode="topic_none", page=1)


# ─────────────────────────────────────────────
#  📣 /log true|false Command
# ─────────────────────────────────────────────
async def cmd_log_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, perm_name="manage_users"):
        return
    schedule_delete(context, update.message.chat_id, update.message.message_id)

    if not context.args:
        sent = await update.message.reply_html(
            "📣 <b>Usage:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "• <code>/log true</code> — Log configured\n"
            "• <code>/log false</code> — Log without"
        )
        schedule_delete(context, sent.chat_id, sent.message_id)
        return

    arg = context.args[0].strip().lower()
    if arg in ("true", "1", "on"):
        await _show_filtered_users(update, context, mode="log_true", page=1)
    elif arg in ("false", "0", "off"):
        await _show_filtered_users(update, context, mode="log_false", page=1)
    else:
        sent = await update.message.reply_html(
            "⚠️ Invalid argument. Please use <code>/log true</code> or <code>/log false</code>."
        )
        schedule_delete(context, sent.chat_id, sent.message_id)


VIEW_USERS_HANDLERS = [
    CommandHandler("find", cmd_find),
    CommandHandler("id", cmd_user_by_id),
    CommandHandler("forward", cmd_forward_filter),
    CommandHandler("topic", cmd_topic_filter),
    CommandHandler("log", cmd_log_filter),
    CallbackQueryHandler(cb_view_users, pattern=r"^view_users$"),
    CallbackQueryHandler(
        cb_view_users_filtered,
        pattern=r"^view_users_(all|expired|banned|forward_true|forward_false|topic_none|log_true|log_false)(:page:\d+)?$",
    ),
    CallbackQueryHandler(cb_noop, pattern=r"^noop$"),
    CallbackQueryHandler(
        cb_user_detail, pattern=r"^user_detail:\d+(?::[a-z_0-9]+)?(?::\d+)?$"
    ),
    CallbackQueryHandler(cb_reauth, pattern=r"^u_reauth_prompt:\d+$"),
    CallbackQueryHandler(
        cb_toggle_fwd, pattern=r"^u_toggle_fwd:\d+:\d+(?::[a-z_0-9]+)?(?::\d+)?$"
    ),
    CallbackQueryHandler(cb_change_topic_menu, pattern=r"^u_change_topic:\d+$"),
    CallbackQueryHandler(cb_set_topic, pattern=r"^u_set_topic:\d+:.*$"),
    CallbackQueryHandler(
        cb_delete_confirm,
        pattern=r"^u_delete_confirm:\d+(?::[a-z_0-9]+)?(?::\d+)?$",
    ),
    CallbackQueryHandler(
        cb_delete_do, pattern=r"^u_delete_do:\d+(?::[a-z_0-9]+)?(?::\d+)?$"
    ),
    CallbackQueryHandler(cb_home, pattern=r"^home$"),
]
