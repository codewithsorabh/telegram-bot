# ─────────────────────────────────────────────
#  templates / inlinebutton / user_inline_button.py
# ─────────────────────────────────────────────

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def AUTH_SUCCESS_KEYBOARD():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Another", callback_data="add_user")],
            [InlineKeyboardButton("👥 View Users", callback_data="view_users")],
        ]
    )


def CANCELLED_KEYBOARD_USER():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")]]
    )


CANCELLED_KEYBOARD = CANCELLED_KEYBOARD_USER


def VIEW_USERS_MAIN_KEYBOARD(counts: dict) -> InlineKeyboardMarkup:
    all_c = counts.get("all", 0)
    exp_c = counts.get("expired", 0)
    ban_c = counts.get("banned", 0)

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"➕ Add New User Account", callback_data="add_user"
                )
            ],
            [
                InlineKeyboardButton(
                    f"📱 All Accounts ({all_c})", callback_data="view_users_all"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⚠️ Expired Sessions ({exp_c})", callback_data="view_users_expired"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🚫 Banned Users ({ban_c})", callback_data="view_users_banned"
                )
            ],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
        ]
    )


def VIEW_USERS_LIST_KEYBOARD(
    users: list[dict], view_mode: str = "all", page: int = 1, page_size: int | None = None
) -> InlineKeyboardMarkup:
    from utils.pagination import paginate, create_pagination_row

    p_data = paginate(users, page=page, page_size=page_size)
    paged_users = p_data["items"]
    rows = []

    for u in paged_users:
        status_flag = (
            "▶" if u.get("forwarding_enabled", u.get("is_forward", True)) else "⏸"
        )
        phone = u.get("phone_number") or u.get("phone", "Unknown")
        if view_mode == "expired":
            label = f"{phone} (Expired)"
        elif view_mode == "banned":
            label = f"{phone} (Banned)"
        elif view_mode == "topic_none":
            label = f"{status_flag} {phone} (No Topic)"
        elif view_mode == "log_true":
            ch = u.get("channel_id")
            label = f"{status_flag} {phone} ({ch})"
        elif view_mode == "log_false":
            label = f"{status_flag} {phone} (No Log Ch)"
        else:
            label = f"{status_flag} {phone}"

        rows.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"user_detail:{u['id']}:{view_mode}:{p_data['page']}"
                )
            ]
        )

    # Add pagination row if multiple pages exist
    pag_row = create_pagination_row(
        current_page=p_data["page"],
        total_pages=p_data["total_pages"],
        callback_prefix=f"view_users_{view_mode}:page",
    )
    if pag_row:
        rows.append(pag_row)

    rows.append(
        [InlineKeyboardButton("⬅️ Back to Manage Users", callback_data="view_users")]
    )
    return InlineKeyboardMarkup(rows)


def VIEW_USERS_KEYBOARD(
    users: list[dict], view_mode: str = "all", counts: dict | None = None, page: int = 1, page_size: int = 5
) -> InlineKeyboardMarkup:
    if view_mode == "main" and counts:
        return VIEW_USERS_MAIN_KEYBOARD(counts)
    return VIEW_USERS_LIST_KEYBOARD(users, view_mode=view_mode, page=page, page_size=page_size)


def USER_DETAIL_KEYBOARD(u: dict, view_mode: str = "all", page: int = 1) -> InlineKeyboardMarkup:
    uid = u["id"]
    clean_mode = view_mode
    if ":" in view_mode:
        parts = view_mode.split(":")
        clean_mode = parts[0]
        try:
            page = int(parts[1])
        except ValueError:
            pass

    back_callback = f"view_users_{clean_mode}:page:{page}" if page > 1 else f"view_users_{clean_mode}"

    is_fwd = u.get("forwarding_enabled", u.get("is_forward", True))
    fwd_btn_label = "⏸️ Stop Forwarding" if is_fwd else "▶️ Start Forwarding"
    fwd_toggle_data = (
        f"u_toggle_fwd:{uid}:0:{clean_mode}:{page}"
        if is_fwd
        else f"u_toggle_fwd:{uid}:1:{clean_mode}:{page}"
    )

    if clean_mode == "expired":
        rows = [
            [InlineKeyboardButton("🔑 Re-Auth", callback_data=f"u_reauth:{uid}")],
            [
                InlineKeyboardButton(
                    "🗑️ Delete Account",
                    callback_data=f"u_delete_confirm:{uid}:{clean_mode}:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back to Expired Users", callback_data=back_callback
                )
            ],
        ]
        return InlineKeyboardMarkup(rows)

    if clean_mode == "banned":
        rows = [
            [
                InlineKeyboardButton(
                    "🗑️ Delete Account",
                    callback_data=f"u_delete_confirm:{uid}:{clean_mode}:{page}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back to Banned Users", callback_data=back_callback
                ),
            ],
        ]
        return InlineKeyboardMarkup(rows)

    rows = [
        [
            InlineKeyboardButton(fwd_btn_label, callback_data=fwd_toggle_data),
        ],
        [
            InlineKeyboardButton(
                "📋 Change Topic", callback_data=f"u_change_topic:{uid}"
            )
        ],
        [
            InlineKeyboardButton(
                "📣 Set Log Channel", callback_data=f"u_change_channel:{uid}"
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑️ Delete Account", callback_data=f"u_delete_confirm:{uid}:{clean_mode}:{page}"
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back to All Users", callback_data=back_callback
            ),
        ],
    ]
    return InlineKeyboardMarkup(rows)
