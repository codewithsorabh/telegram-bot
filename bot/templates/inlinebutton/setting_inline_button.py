from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def CANCELLED_KEYBOARD_SETTINGS():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚙️ Settings", callback_data="manage_settings")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
        ]
    )


def SETTINGS_KEYBOARD() -> InlineKeyboardMarkup:
    from models.config import (
        get_owner_log_channel,
        get_page_size,
        get_all_admins,
        is_plan_step_enabled,
        is_topic_step_enabled,
    )

    t_flag = "ON" if is_topic_step_enabled() else "OFF"
    p_flag = "ON" if is_plan_step_enabled() else "OFF"

    owner_ch = get_owner_log_channel()
    ch_str = owner_ch if owner_ch else "Not Set"
    page_size = get_page_size()
    total_admins = len(get_all_admins())

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"👥 View Admins ({total_admins})",
                    callback_data="settings_view_admins",
                ),
                InlineKeyboardButton(
                    "➕ Add Sec Admin",
                    callback_data="settings_add_admin",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"📄 Items Per Page: {page_size}",
                    callback_data="settings_edit_page_size",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"📋 Step Topic: {t_flag}",
                    callback_data="settings_toggle:SELECT_STEP_TOPIC",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"👑 Owner Log Channel: {ch_str}",
                    callback_data="settings_edit_owner_ch",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back to Menu",
                    callback_data="home",
                ),
            ],
        ]
    )
