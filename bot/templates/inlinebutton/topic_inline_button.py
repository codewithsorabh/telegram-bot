# ─────────────────────────────────────────────
#  templates / inlinebutton / topic_inline_button.py
# ─────────────────────────────────────────────

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def CANCELLED_KEYBOARD_TOPIC():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Topic", callback_data="add_topic")],
            [InlineKeyboardButton("📋 View Topics", callback_data="view_topics")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
        ]
    )


def MANAGE_TOPICS_KEYBOARD() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Topic", callback_data="add_topic")],
            [InlineKeyboardButton("📋 View Topics", callback_data="view_topics")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
        ]
    )


def TOPIC_DETAIL_KEYBOARD(
    topic_id: int, url_key: str = "", page: int = 1
) -> InlineKeyboardMarkup:
    back_callback = f"view_topics:page:{page}" if page > 1 else "view_topics"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Add URL", callback_data=f"t_addurl_key:{url_key}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑️ Delete URL", callback_data=f"t_del_entry:{topic_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑️ Delete Category", callback_data=f"t_del_key:{url_key}"
                )
            ],
            [InlineKeyboardButton("⬅️ Back to Topics", callback_data=back_callback)],
        ]
    )


def VIEW_TOPICS_KEYBOARD(
    summaries: list[dict], page: int = 1, page_size: int | None = None
) -> InlineKeyboardMarkup:
    from utils.pagination import paginate, create_pagination_row

    p_data = paginate(summaries, page=page, page_size=page_size)
    paged_summaries = p_data["items"]
    rows = []

    for s in paged_summaries:
        label_btn = f"📋 {s['name']} ({s['url_key']}) — {s['total_urls']} URL(s)"
        rows.append(
            [
                InlineKeyboardButton(
                    label_btn,
                    callback_data=f"topic_key_detail:{s['url_key']}:{p_data['page']}",
                )
            ]
        )

    pag_row = create_pagination_row(
        current_page=p_data["page"],
        total_pages=p_data["total_pages"],
        callback_prefix="view_topics:page",
    )
    if pag_row:
        rows.append(pag_row)

    rows.append(
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="manage_topics")]
    )
    return InlineKeyboardMarkup(rows)


def TOPIC_DEL_URL_KEYBOARD(url_key: str, entries: list[dict]) -> InlineKeyboardMarkup:
    from utils.url_validator import label

    rows = []
    for i, e in enumerate(entries):
        lbl = f"Remove: {i+1}. {label(e.get('url', ''))}"
        if len(lbl) > 40:
            lbl = lbl[:37] + "..."
        rows.append(
            [
                InlineKeyboardButton(
                    lbl, callback_data=f"t_del_entry_id:{e['id']}:{url_key}"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("⬅️ Back", callback_data=f"topic_key_detail:{url_key}")]
    )
    return InlineKeyboardMarkup(rows)


# Legacy alias
MANAGE_GROUPS_KEYBOARD = MANAGE_TOPICS_KEYBOARD
