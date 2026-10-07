# ─────────────────────────────────────────────
#  handlers / topics / view_topic.py
#
#  View Topics flow (url_key category management):
#    • List topic categories by url_key
#    • Category detail: url_key, URLs, status, interval
#    • Add URL to existing category
#    • Delete individual URL entry
#    • Delete category
# ─────────────────────────────────────────────

import warnings

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"telegram\.ext\._conversationhandler",
)

from config.logger import LOG, log
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    CommandHandler,
)

from models.topics import (
    get_all_topics,
    get_topic_by_id,
    get_topics_by_key,
    get_topic_keys_summary,
    create_topic_entry,
    delete_topic_entry,
    delete_topics_by_key,
)
from utils.auto_delete import schedule_delete
from utils.url_validator import parse_url
from templates.messages import (
    MANAGE_TOPICS_HEADER,
    TOPIC_DETAIL_MSG,
    TOPIC_GROUP_DETAIL_MSG,
    CANCELLED_MSG,
)
from templates.inlinebutton import (
    MANAGE_TOPICS_KEYBOARD,
    VIEW_TOPICS_KEYBOARD,
    TOPIC_DETAIL_KEYBOARD,
    TOPIC_DEL_URL_KEYBOARD,
    CANCEL_KEYBOARD,
    CANCELLED_KEYBOARD_TOPIC,
)

STATE_ADDURL_TEXT = 300

_KEY_MSG_ID     = "au_msg_id"
_KEY_CHAT_ID    = "au_chat_id"
_KEY_ACTIVE_KEY = "vt_active_ukey"


async def _edit_bubble(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard=None,
    update: Update | None = None,
) -> None:
    chat_id = context.user_data.get(_KEY_CHAT_ID)
    msg_id  = context.user_data.get(_KEY_MSG_ID)
    if (not chat_id or not msg_id) and update and update.callback_query and update.callback_query.message:
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
    for key in (_KEY_MSG_ID, _KEY_CHAT_ID, _KEY_ACTIVE_KEY):
        context.user_data.pop(key, None)


async def _safe_answer(query, text: str | None = None, show_alert: bool = False) -> None:
    if not query:
        return
    try:
        if text:
            await query.answer(text, show_alert=show_alert)
        else:
            await query.answer()
    except Exception:
        pass


async def cb_manage_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from models.config import check_user_permission
    if not await check_user_permission(update, perm_name="manage_topics"):
        return

    query = update.callback_query
    await _safe_answer(query)

    context.user_data[_KEY_MSG_ID]  = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    await _edit_bubble(context, MANAGE_TOPICS_HEADER, MANAGE_TOPICS_KEYBOARD(), update=update)


async def cb_view_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await _safe_answer(query)
        if query.message:
            context.user_data[_KEY_MSG_ID]  = query.message.message_id
            context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    data = query.data or ""
    page = 1
    if ":page:" in data:
        try:
            page = int(data.split(":page:")[1])
        except ValueError:
            page = 1

    summaries = get_topic_keys_summary()
    from models.config import get_page_size
    page_size = get_page_size()
    from utils.pagination import paginate
    p_data = paginate(summaries, page=page, page_size=page_size)

    header = f"📋 <b>Topic Categories ({p_data['total_items']})</b>"
    if p_data["total_pages"] > 1:
        header += f" — Page {p_data['page']}/{p_data['total_pages']} ({p_data['start_idx']}-{p_data['end_idx']})"

    text = f"{header}\n\nSelect a category to view or edit destination URLs:"

    await _edit_bubble(
        context,
        text,
        VIEW_TOPICS_KEYBOARD(summaries, page=p_data['page'], page_size=page_size),
        update=update,
    )


async def cb_topic_key_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    parts = query.data.split(":")
    url_key = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    entries = get_topics_by_key(url_key)

    context.user_data[_KEY_MSG_ID]  = query.message.message_id
    context.user_data[_KEY_CHAT_ID] = query.message.chat_id

    msg_text = TOPIC_GROUP_DETAIL_MSG(url_key, entries)
    first_id = entries[0]["id"] if entries else 0
    kb = TOPIC_DETAIL_KEYBOARD(first_id, url_key, page=page)

    await _edit_bubble(context, msg_text, kb, update=update)


async def cb_delete_topic_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    url_key = query.data.split(":")[1]
    delete_topics_by_key(url_key)
    await _safe_answer(query, f"Category '{url_key}' deleted.", show_alert=True)

    await cb_view_topics(update, context)


async def cb_del_entry_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    try:
        topic_id = int(query.data.split(":")[1])
    except Exception:
        return

    topic = get_topic_by_id(topic_id)
    if not topic:
        return

    url_key = topic["url_key"]
    entries = get_topics_by_key(url_key)

    await _edit_bubble(
        context,
        f"❌ <b>Remove URL from {url_key}</b>\n\nTap a URL below to remove it:",
        TOPIC_DEL_URL_KEYBOARD(url_key, entries),
    )


async def cb_del_entry_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split(":")
    entry_id = int(parts[1])
    url_key  = parts[2]

    delete_topic_entry(entry_id)
    await _safe_answer(query, "URL removed.", show_alert=True)

    entries = get_topics_by_key(url_key)
    msg_text = TOPIC_GROUP_DETAIL_MSG(url_key, entries)
    first_id = entries[0]["id"] if entries else 0
    kb = TOPIC_DETAIL_KEYBOARD(first_id, url_key)

    await _edit_bubble(context, msg_text, kb)


async def cb_addurl_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)

    url_key = query.data.split(":")[1]
    context.user_data[_KEY_MSG_ID]     = query.message.message_id
    context.user_data[_KEY_CHAT_ID]    = query.message.chat_id
    context.user_data[_KEY_ACTIVE_KEY] = url_key

    await _edit_bubble(
        context,
        f"➕ <b>Add URL to {url_key}</b>\n\n"
        "Enter Telegram <b>Destination</b> link(s) <b>line-by-line</b>:\n"
        "(Optional: specify <code> - interval</code> in seconds per line)\n\n"
        "<code>https://t.me/groups1 - 60</code>\n"
        "<code>https://t.me/groups2 - 120</code>",
        CANCEL_KEYBOARD(),
    )
    return STATE_ADDURL_TEXT


async def recv_addurl_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    text = update.message.text.strip()
    url_key = context.user_data.pop(_KEY_ACTIVE_KEY, None)

    if not url_key:
        _cleanup(context)
        return ConversationHandler.END

    import re
    entries = get_topics_by_key(url_key)
    name = entries[0]["name"] if entries else url_key.capitalize()
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    for line in lines:
        m = re.match(r"^(.*?)\s*[-:]\s*(\d+)\s*$", line)
        if m and m.group(1).strip():
            raw_url = m.group(1).strip()
            interval = int(m.group(2))
        else:
            raw_url = line
            interval = 60

        parsed = parse_url(raw_url)
        if parsed.get("type") != "invalid":
            create_topic_entry(
                url_key=url_key,
                name=name,
                url=parsed.get("normalized") or raw_url,
                enabled=True,
                interval_seconds=interval,
            )

    updated_entries = get_topics_by_key(url_key)
    msg_text = TOPIC_GROUP_DETAIL_MSG(url_key, updated_entries)
    first_id = updated_entries[0]["id"] if updated_entries else 0
    kb = TOPIC_DETAIL_KEYBOARD(first_id, url_key)

    await _edit_bubble(context, msg_text, kb)
    _cleanup(context)
    return ConversationHandler.END


async def cmd_topic_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _auto_delete_user_msg(update, context)
    await _edit_bubble(context, CANCELLED_MSG, CANCELLED_KEYBOARD_TOPIC())
    _cleanup(context)
    return ConversationHandler.END


def build_view_topic_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_addurl_start, pattern=r"^t_addurl_key:[A-Za-z0-9_]+$"),
        ],
        states={
            STATE_ADDURL_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_addurl_text)],
        },
        fallbacks=[CommandHandler("cancel", cmd_topic_cancel)],
        allow_reentry=True,
    )


VIEW_TOPIC_HANDLERS = [
    CallbackQueryHandler(cb_manage_topics,   pattern=r"^manage_topics$"),
    CallbackQueryHandler(cb_view_topics,     pattern=r"^view_topics(:page:\d+)?$"),
    CallbackQueryHandler(cb_topic_key_detail,pattern=r"^topic_key_detail:[A-Za-z0-9_]+(?::\d+)?$"),
    CallbackQueryHandler(cb_delete_topic_key,pattern=r"^t_del_key:[A-Za-z0-9_]+$"),
    CallbackQueryHandler(cb_del_entry_list,  pattern=r"^t_del_entry:\d+$"),
    CallbackQueryHandler(cb_del_entry_do,    pattern=r"^t_del_entry_id:\d+:[A-Za-z0-9_]+$"),
]
