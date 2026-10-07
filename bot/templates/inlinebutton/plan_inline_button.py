# ─────────────────────────────────────────────
#  templates / inlinebutton / plan_inline_button.py
# ─────────────────────────────────────────────

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def CANCELLED_KEYBOARD_PLAN():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Plan", callback_data="add_plan")],
            [InlineKeyboardButton("💳 View Plans", callback_data="view_plans")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
        ]
    )


def MANAGE_PLANS_KEYBOARD() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Plan", callback_data="add_plan")],
            [InlineKeyboardButton("💳 View Plans", callback_data="view_plans")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
        ]
    )


def PLAN_DETAIL_KEYBOARD(plan_id: int, page: int = 1) -> InlineKeyboardMarkup:
    back_callback = f"view_plans:page:{page}" if page > 1 else "view_plans"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑️ Delete Plan", callback_data=f"p_delete:{plan_id}:{page}"
                ),
            ],
            [InlineKeyboardButton("⬅️ Back to Plans", callback_data=back_callback)],
        ]
    )


def VIEW_PLANS_KEYBOARD(plans: list[dict], page: int = 1, page_size: int | None = None) -> InlineKeyboardMarkup:
    from utils.pagination import paginate, create_pagination_row

    p_data = paginate(plans, page=page, page_size=page_size)
    paged_plans = p_data["items"]
    rows = []

    for p in paged_plans:
        price_tag = f"${p.get('price', 0):.2f}" if p.get("price") else "Free"
        rows.append(
            [
                InlineKeyboardButton(
                    f"💳 {p.get('name', 'Plan #'+str(p['id']))} ({p['validity_days']}d) {price_tag}",
                    callback_data=f"plan_detail:{p['id']}:{p_data['page']}",
                )
            ]
        )

    pag_row = create_pagination_row(
        current_page=p_data["page"],
        total_pages=p_data["total_pages"],
        callback_prefix="view_plans:page",
    )
    if pag_row:
        rows.append(pag_row)

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="manage_plans")])
    return InlineKeyboardMarkup(rows)
