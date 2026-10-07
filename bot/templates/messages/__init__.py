# ─────────────────────────────────────────────
#  templates / messages / __init__.py
# ─────────────────────────────────────────────

from templates.messages.common_messages import (
    UNAUTHORIZED_MSG,
    AUTH_FAILED,
    INVALID_API_ID,
    CANCELLED_MSG,
)
from templates.messages.user_messages import (
    WELCOME_MSG,
    ASK_AUTH,
    ASK_OTP,
    ASK_2FA,
    AUTH_SUCCESS_MSG,
    VIEW_USERS_HEADER,
    VIEW_USERS_EMPTY,
    USER_DETAIL_MSG,
)
from templates.messages.topic_messages import (
    MANAGE_TOPICS_HEADER,
    ASK_TOPIC_NAME,
    ASK_TOPIC_URLS,
    TOPIC_DETAIL_MSG,
    TOPIC_GROUP_DETAIL_MSG,
    MANAGE_GROUPS_HEADER,
    ASK_GROUP_NAME,
    ASK_GROUP_URLS,
)
from templates.messages.plan_messages import (
    MANAGE_PLANS_HEADER,
    PLAN_DETAIL_MSG,
)
from templates.messages.setting_messages import (
    SETTINGS_HEADER,
    SETTINGS_UNAUTHORIZED_MSG,
)

# Re-export inline buttons for backward compatibility
from templates.inlinebutton import (
    WELCOME_KEYBOARD,
    CANCEL_KEYBOARD,
    AUTH_SUCCESS_KEYBOARD,
    CANCELLED_KEYBOARD_USER,
    CANCELLED_KEYBOARD,
    VIEW_USERS_MAIN_KEYBOARD,
    VIEW_USERS_LIST_KEYBOARD,
    VIEW_USERS_KEYBOARD,
    USER_DETAIL_KEYBOARD,
    CANCELLED_KEYBOARD_TOPIC,
    MANAGE_TOPICS_KEYBOARD,
    TOPIC_DETAIL_KEYBOARD,
    VIEW_TOPICS_KEYBOARD,
    TOPIC_DEL_URL_KEYBOARD,
    MANAGE_GROUPS_KEYBOARD,
    CANCELLED_KEYBOARD_PLAN,
    MANAGE_PLANS_KEYBOARD,
    PLAN_DETAIL_KEYBOARD,
    VIEW_PLANS_KEYBOARD,
    CANCELLED_KEYBOARD_SETTINGS,
    SETTINGS_KEYBOARD,
)
