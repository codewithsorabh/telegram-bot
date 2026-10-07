# ─────────────────────────────────────────────
#  handlers / settings / manage_settings.py
#
#  Handles Settings & Admin Management:
#    • View settings menu
#    • View primary & secondary admins
#    • Add secondary admin (ConversationHandler)
#    • Remove secondary admin
# ─────────────────────────────────────────────

import warnings

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"telegram\.ext\._conversationhandler",
)

from config.logger import LOG, log
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    CommandHandler,
)

from models.config import (
    get_all_admins,
    add_secondary_admin,
    remove_secondary_admin,
    get_primary_admin,
)
from utils.auto_delete import schedule_delete
from templates.messages import (
    SETTINGS_HEADER,
    CANCELLED_MSG,
)
from templates.inlinebutton import (
    SETTINGS_KEYBOARD,
    CANCEL_KEYBOARD,
    CANCELLED_KEYBOARD_SETTINGS,
)

STATE_ADD_ADMIN_ID = 300
STATE_ADD_ADMIN_NAME = 302
STATE_EDIT_PAGE_SIZE = 303

_KEY_MSG_ID = "au_msg_id"
_KEY_CHAT_ID = "au_chat_id"


async def _edit_bubble(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard=None,
    update: Update | None = None,
) -> None:
    chat_id = context.user_data.get(_KEY_CHAT_ID)
    msg_id = context.user_data.get(_KEY_MSG_ID)
    if (
        (not chat_id or not msg_id)
        and update
        and update.callback_query
        and update.callback_query.message
    ):
        chat_id = update.callback_query.message.chat_id
        msg_id = update.callback_query.message.message_id
        context.user_data[_KEY_CHAT_ID] = chat_id
        context.user_data[_KEY_MSG_ID] = msg_id

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
    for key in (_KEY_MSG_ID, _KEY_CHAT_ID, "new_admin_id"):
        context.user_data.pop(key, None)


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


async def cb_manage_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission

    if not await check_user_permission(update, is_settings=True):
        return

    query = update.callback_query
    await _safe_answer(query)

    context.user_data[_KEY_MSG_ID] = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    await _edit_bubble(context, SETTINGS_HEADER, SETTINGS_KEYBOARD(), update=update)


async def _show_admin_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1
):
    query = update.callback_query
    await _safe_answer(query)

    if query and query.message:
        context.user_data[_KEY_MSG_ID] = query.message.message_id
        context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    # Filter out primary admins so only secondary admins appear in buttons list
    all_admins = get_all_admins()
    admins = [a for a in all_admins if not a.get("is_primary")]

    from models.config import get_page_size

    page_size = get_page_size()
    from utils.pagination import paginate, create_pagination_row

    p_data = paginate(admins, page=page, page_size=page_size)
    paged_admins = p_data["items"]

    buttons = []
    admin_lines = []

    for i, a in enumerate(paged_admins):
        item_num = p_data["start_idx"] + i
        aid = a["admin_id"]
        aname = a.get("admin_name") or "Admin"

        # admin_lines.append(f"<b>{item_num}.</b> {aname} (<code>{aid}</code>)")

        btn_label = f"{aname} ({aid})"
        buttons.append(
            [
                InlineKeyboardButton(
                    btn_label,
                    callback_data=f"admin_detail:{aid}:page:{p_data['page']}",
                )
            ]
        )

    pag_row = create_pagination_row(
        current_page=p_data["page"],
        total_pages=p_data["total_pages"],
        callback_prefix="settings_view_admins:page",
    )
    if pag_row:
        buttons.append(pag_row)

    buttons.append(
        [InlineKeyboardButton("⬅️ Back to Settings", callback_data="manage_settings")]
    )

    header = f"👥 <b>Admin Accounts & Permissions ({p_data['total_items']})</b>"
    if p_data["total_pages"] > 1:
        header += f" — Page {p_data['page']}/{p_data['total_pages']} ({p_data['start_idx']}-{p_data['end_idx']})"

    if not admins:
        text = (
            f"{header}\n━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No secondary admin accounts found.</i>\n\n"
            "Click <b>➕ Add Sec Admin</b> in Settings to add secondary admins."
        )
    else:
        text = (
            f"{header}\n━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(admin_lines)
            + "\n━━━━━━━━━━━━━━━━━━━━━\n"
            "Select an admin account below to view or edit permissions:"
        )

    await _edit_bubble(context, text, InlineKeyboardMarkup(buttons), update=update)


async def cb_view_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    page = 1
    if ":page:" in data:
        try:
            page = int(data.split(":page:")[1])
        except ValueError:
            page = 1
    await _show_admin_list(update, context, page=page)


async def _show_admin_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, aid: int, page: int = 1
):
    query = update.callback_query
    await _safe_answer(query)

    admins = get_all_admins()
    target_admin = next((a for a in admins if a["admin_id"] == aid), None)

    if not target_admin:
        await _safe_answer(query, "⚠️ Admin account not found.", show_alert=True)
        return await _show_admin_list(update, context, page=page)

    aname = target_admin.get("admin_name") or f"Admin {aid}"
    perms = target_admin.get("permissions", {})
    u_flag = "Allow" if perms.get("manage_users", True) else "Deny"
    t_flag = "Allow" if perms.get("manage_topics", True) else "Deny"

    u_icon = "✅" if u_flag == "Allow" else "❌"
    t_icon = "✅" if t_flag == "Allow" else "❌"

    text = (
        f"👤 <b>Admin Details: {aname}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>Admin ID:</b> <code>{aid}</code>\n\n"
        f" - <b>Users:</b> {u_flag}\n"
        f" - <b>Topics:</b> {t_flag}"
    )

    buttons = [
        [
            InlineKeyboardButton(
                f"Users: {u_icon} {u_flag}",
                callback_data=f"toggle_perm:{aid}:manage_users:{page}",
            )
        ],
        [
            InlineKeyboardButton(
                f"Topics: {t_icon} {t_flag}",
                callback_data=f"toggle_perm:{aid}:manage_topics:{page}",
            )
        ],
        [
            InlineKeyboardButton(
                f"❌ Delete Admin",
                callback_data=f"remove_admin:{aid}:{page}",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data=f"settings_view_admins:page:{page}",
            )
        ],
    ]

    await _edit_bubble(context, text, InlineKeyboardMarkup(buttons), update=update)


async def cb_admin_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    parts = data.split(":")
    aid = int(parts[1])
    page = 1
    if len(parts) >= 4 and parts[2] == "page" and parts[3].isdigit():
        page = int(parts[3])
    await _show_admin_detail(update, context, aid=aid, page=page)


async def cb_toggle_admin_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    try:
        parts = query.data.split(":")
        aid = int(parts[1])
        perm_name = parts[2]
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    except Exception:
        return

    from models.config import toggle_admin_permission

    toggle_admin_permission(aid, perm_name)
    await _show_admin_detail(update, context, aid=aid, page=page)


async def cb_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    try:
        parts = query.data.split(":")
        aid = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    except (IndexError, ValueError):
        await _safe_answer(query, "Invalid request.", show_alert=True)
        return

    if remove_secondary_admin(aid):
        await _safe_answer(query, "✅ Admin removed.", show_alert=True)
        await _show_admin_list(update, context, page=page)
    else:
        await _safe_answer(query, "⛔ Cannot remove primary admin.", show_alert=True)
        await _show_admin_detail(update, context, aid=aid, page=page)


async def cb_toggle_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    key = query.data.split(":")[1]
    from models.config import toggle_setting

    new_val = toggle_setting(key)
    status_str = "ON" if new_val else "OFF"
    await _safe_answer(
        query,
        f"{key.replace('SELECT_STEP_', '')} Step is now {status_str}",
        show_alert=True,
    )
    context.user_data[_KEY_MSG_ID] = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id
    await _edit_bubble(context, SETTINGS_HEADER, SETTINGS_KEYBOARD())


async def cb_add_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    context.user_data[_KEY_MSG_ID] = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    await _edit_bubble(
        context,
        "➕ <b>Add Secondary Admin — Step 1 / 2</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🆔 <b>Admin User ID</b>\n\n"
        "Enter Telegram User ID of the new secondary admin (numbers only):",
        CANCEL_KEYBOARD(),
    )
    return STATE_ADD_ADMIN_ID


async def recv_add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    text = update.message.text.strip()

    if not text.isdigit():
        await _edit_bubble(
            context,
            "⚠️ <b>Invalid User ID.</b> Must be numbers only.\n\nPlease try again:",
            CANCEL_KEYBOARD(),
        )
        return STATE_ADD_ADMIN_ID

    context.user_data["new_admin_id"] = int(text)

    await _edit_bubble(
        context,
        "➕ <b>Add Secondary Admin — Step 2 / 2</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "👤 <b>Admin Name / Label</b>\n\n"
        "Enter a name or label for this admin:\n"
        "(e.g. <code>John Doe</code> or <code>@adminusername</code>)",
        CANCEL_KEYBOARD(),
    )
    return STATE_ADD_ADMIN_NAME


async def recv_add_admin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    admin_name = update.message.text.strip()
    aid = context.user_data.get("new_admin_id")

    if not aid:
        await _edit_bubble(
            context,
            "⚠️ <b>Session expired.</b> Please try again.",
            SETTINGS_KEYBOARD(),
        )
        _cleanup(context)
        return ConversationHandler.END

    if add_secondary_admin(aid, admin_name):
        await _edit_bubble(
            context,
            f"✅ <b>Secondary Admin Added Successfully!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Name:</b> {admin_name}\n"
            f"🆔 <b>User ID:</b> <code>{aid}</code>",
            SETTINGS_KEYBOARD(),
        )
    else:
        await _edit_bubble(
            context,
            "⚠️ <b>Failed to add secondary admin.</b>\n\nPlease check database connection.",
            SETTINGS_KEYBOARD(),
        )

    _cleanup(context)
    return ConversationHandler.END


# ── Owner Log Channel Conversation ─────────────────────────────
STATE_EDIT_OWNER_CH = 301


async def cb_edit_owner_ch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data[_KEY_MSG_ID] = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    from models.config import get_owner_log_channel

    curr = get_owner_log_channel()

    await _edit_bubble(
        context,
        "👑 <b>Set Owner Log Channel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Current: <code>{curr or 'Not set'}</code>\n\n"
        "Enter Telegram Channel ID for Owner Notifications\n"
        "(e.g. <code>-1001234567890</code> or <code>0</code> to clear):",
        CANCEL_KEYBOARD(),
    )
    return STATE_EDIT_OWNER_CH


async def recv_owner_ch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    text = update.message.text.strip()

    from models.config import set_owner_log_channel

    if text == "0":
        set_owner_log_channel("")
        await _edit_bubble(
            context,
            "✅ <b>Owner Log Channel Cleared!</b>",
            SETTINGS_KEYBOARD(),
        )
    else:
        from models.users import normalize_channel_id

        norm = normalize_channel_id(text)
        if not norm:
            await _edit_bubble(
                context,
                "⚠️ <b>Invalid Channel ID.</b> Must be numbers (e.g. <code>-1001234567890</code> or <code>0</code> to clear):\n\nPlease try again:",
                CANCEL_KEYBOARD(),
            )
            return STATE_EDIT_OWNER_CH

        set_owner_log_channel(norm)
        await _edit_bubble(
            context,
            f"✅ <b>Owner Log Channel Set!</b>\n\nChannel ID: <code>{norm}</code>",
            SETTINGS_KEYBOARD(),
        )

    _cleanup(context)
    return ConversationHandler.END


async def cb_edit_page_size_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission, get_page_size

    if not await check_user_permission(update, is_settings=True):
        return ConversationHandler.END

    query = update.callback_query
    await _safe_answer(query)

    context.user_data[_KEY_MSG_ID] = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    cur_size = get_page_size()
    text = (
        "📄 <b>Set Items Per Page</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Current page size: <b>{cur_size}</b> items\n\n"
        "Please enter the number of items per page to show across all lists:\n"
        "<i>(Enter a number between <b>10</b> and <b>70</b>)</i>"
    )
    await _edit_bubble(context, text, CANCEL_KEYBOARD(), update=update)
    return STATE_EDIT_PAGE_SIZE


async def recv_page_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    text = update.message.text.strip()

    if not text.isdigit() or int(text) < 10 or int(text) > 70:
        await _edit_bubble(
            context,
            "⚠️ <b>Invalid Page Size!</b>\n\n"
            "Please enter a valid number between <b>10</b> and <b>70</b>:\n\n"
            "Try again:",
            CANCEL_KEYBOARD(),
        )
        return STATE_EDIT_PAGE_SIZE

    new_size = int(text)
    from models.config import set_page_size

    set_page_size(new_size)

    await _edit_bubble(
        context,
        f"✅ <b>Page Size Updated!</b>\n\nNew page size: <code>{new_size}</code> items per page.",
        SETTINGS_KEYBOARD(),
    )
    _cleanup(context)
    return ConversationHandler.END


async def cb_settings_cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await _safe_answer(query, "Cancelled.")
    _cleanup(context)
    await cb_manage_settings(update, context)
    return ConversationHandler.END


async def cmd_settings_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    _cleanup(context)
    await _edit_bubble(context, SETTINGS_HEADER, SETTINGS_KEYBOARD())
    return ConversationHandler.END


def build_manage_settings_conv() -> ConversationHandler:
    cancel_cb = CallbackQueryHandler(
        cb_settings_cancel_button, pattern=r"^(cancel|cancel_.*|global_cancel)$"
    )
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_add_admin_start, pattern=r"^settings_add_admin$"),
            CallbackQueryHandler(
                cb_edit_owner_ch_start, pattern=r"^settings_edit_owner_ch$"
            ),
            CallbackQueryHandler(
                cb_edit_page_size_start, pattern=r"^settings_edit_page_size$"
            ),
        ],
        states={
            STATE_ADD_ADMIN_ID: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_add_admin_id),
            ],
            STATE_ADD_ADMIN_NAME: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_add_admin_name),
            ],
            STATE_EDIT_OWNER_CH: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_owner_ch),
            ],
            STATE_EDIT_PAGE_SIZE: [
                cancel_cb,
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_page_size),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_settings_cancel),
            cancel_cb,
        ],
        allow_reentry=True,
    )


SETTINGS_HANDLERS = [
    CallbackQueryHandler(cb_manage_settings, pattern=r"^manage_settings$"),
    CallbackQueryHandler(cb_view_admins, pattern=r"^settings_view_admins(:page:\d+)?$"),
    CallbackQueryHandler(cb_admin_detail, pattern=r"^admin_detail:\d+(?::page:\d+)?$"),
    CallbackQueryHandler(
        cb_toggle_admin_perm, pattern=r"^toggle_perm:\d+:[a-z_]+(?::\d+)?$"
    ),
    CallbackQueryHandler(cb_remove_admin, pattern=r"^remove_admin:\d+(?::\d+)?$"),
    CallbackQueryHandler(
        cb_toggle_setting,
        pattern=r"^settings_toggle:(SELECT_STEP_TOPIC|SELECT_STEP_PLAN)$",
    ),
]
