# ─────────────────────────────────────────────
#  templates / inlinebutton / common_inline_button.py
# ─────────────────────────────────────────────

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def WELCOME_KEYBOARD(user_id: int | str = 0) -> InlineKeyboardMarkup:
    from models.config import is_primary_admin, get_admin_permissions

    is_primary = is_primary_admin(user_id) if user_id else False
    perms = (
        get_admin_permissions(user_id)
        if user_id
        else {
            "manage_users": True,
            "manage_topics": True,
        }
    )

    available_btns = []

    if is_primary or perms.get("manage_users", True):
        available_btns.append(
            [InlineKeyboardButton("👥 Manage Users", callback_data="view_users")]
        )

    if is_primary or perms.get("manage_topics", True):
        available_btns.append(
            [InlineKeyboardButton("📋 Manage Topics", callback_data="manage_topics")]
        )

    if is_primary:
        available_btns.append(
            [InlineKeyboardButton("⚙️ Settings", callback_data="manage_settings")]
        )

    return InlineKeyboardMarkup(available_btns)


def CANCEL_KEYBOARD():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_user"),
            ]
        ]
    )
