# ─────────────────────────────────────────────
#  templates / messages / user_messages.py
# ─────────────────────────────────────────────

from utils.date_formatter import format_ist_date, format_ist_time, format_ist_datetime
from datetime import datetime, timezone


def WELCOME_MSG(user):
    name = user.first_name if user.first_name else "Friend"
    return (
        f"👋 <b>Hello, {name}!</b>\n\n"
        "🤖 <b>Welcome to AI Bot Control Center</b>\n\n"
        "Manage users, topics, plans, and system settings below 👇\n\n"
        "⚙️ <b>Use the buttons below to manage the system.</b>"
    )


ASK_AUTH = (
    "➕ <b>Add User — Step 1 / 2</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "📋 <b>Credentials</b>\n\n"
    "Enter your credentials <b>line-by-line</b>:\n\n"
    "<code>API_ID</code>\n"
    "<code>API_HASH</code>\n"
    "<code>+PHONE_NUMBER</code>\n\n"
    "Example:\n"
    "<code>12345678</code>\n"
    "<code>abcdef1234567890abcdef1234567890</code>\n"
    "<code>+919876543210</code>\n\n"
    "👉 Get API ID & Hash from <a href='https://my.telegram.org/apps'>my.telegram.org/apps</a>"
)


def ASK_OTP(phone: str = "") -> str:
    phone_display = f" for <code>{phone}</code>" if phone else ""
    return (
        f"✉️ <b>Add User — Step 2 / 2: Enter OTP Code{phone_display}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Verification code has been sent to your Telegram app.\n\n"
        "👉 <b>Type or paste the numeric code directly into this chat:</b>"
    )


ASK_2FA = (
    "🔒 <b>Two-Factor Authentication</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "Your account has 2FA enabled.\n\n"
    "Please enter your <b>cloud password</b> to continue."
)


def AUTH_SUCCESS_MSG(phone: str) -> str:
    return (
        "✅ <b>Account linked!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <code>{phone}</code> has been authenticated\n"
        "and stored securely in the database. 🎉\n\n"
        "Choose an option below 👇"
    )


VIEW_USERS_HEADER = (
    "👥 <b>Linked User Accounts</b>\n\nSelect an account to view or edit controls:"
)

VIEW_USERS_EMPTY = (
    "👥 <b>Linked User Accounts</b>\n\n"
    "📭 No accounts linked yet.\n"
    "Tap <b>➕ Add User</b> to link one."
)


def USER_DETAIL_MSG(u: dict) -> str:
    added_at_date = format_ist_date(u["created_at"]) if u.get("created_at") else "—"
    added_at_time = format_ist_time(u["created_at"]) if u.get("created_at") else "—"

    plan_st = u.get("plan_started_at") or u.get("plan_st")
    plan_st_str = format_ist_datetime(plan_st) if plan_st else None
    expired_at = u.get("plan_expired_at")
    now = datetime.now(timezone.utc)
    if expired_at:
        if expired_at.tzinfo is None:
            from datetime import timezone as tz

            expired_at = expired_at.replace(tzinfo=tz.utc)
        days_left = (expired_at - now).days
        exp_str = format_ist_datetime(expired_at)
        days_badge = (
            f"☑ {days_left}d left"
            if days_left > 3
            else (f"✔ {days_left}d left" if days_left > 0 else "✗ Expired")
        )
        plan_exp_str = f"{exp_str} ({days_badge})"
    else:
        plan_exp_str = "♾️ <b>Unlimited</b> (No Expiration)"

    # topic_id stores the url_key string
    topic_key = u.get("topic_id") or ""
    topic_name = u.get("topic_name") or (
        f"<code>{topic_key}</code>" if topic_key else "<i>None</i>"
    )
    plan_name = u.get("plan_name")
    val_days = u.get("validity_days")
    plan_str = (
        f"{plan_name} ({val_days}d)"
        if val_days
        else "<i>No Plan Selected (Unlimited)</i>"
    )
    phone = u.get("phone_number") or u.get("phone", "—")
    fwd_val = u.get("forwarding_enabled")
    if fwd_val is None:
        fwd_val = u.get("is_forward")
    fwd_enabled = True if fwd_val is None else bool(fwd_val)
    fwd_status = "▶ Running" if fwd_enabled else "⏸ Stopped"
    user_id = u.get("user_id") or u.get("added_by", "—")
    validate = (u.get("validate") or u.get("session_status") or "active").lower()
    if validate == "active":
        session_status = "☑ <b>Active</b>"
    elif validate == "banned":
        session_status = "🚫 <b>Banned</b>"
    elif validate == "suspended":
        session_status = "⏸ <b>Suspended</b>"
    elif validate == "expired":
        session_status = "✗ <b>Expired</b>"
    else:
        session_status = f"ℹ️ <b>{validate.capitalize()}</b>"

    # Look up source_url from topics table for this key
    src_display = "<i>Not set</i>"
    if topic_key:
        try:
            from models.topics import get_source_url_for_key

            src = get_source_url_for_key(topic_key)
            src_display = f"<code>{src}</code>" if src else "<i>Not set</i>"
        except Exception:
            pass

    ch_id = u.get("channel_id")
    ch_display = f"<code>{ch_id}</code>" if ch_id else "<i>Not set</i>"

    return (
        "📋 <b>Account Details & Controls</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Phone :</b>      <code>{phone}</code>\n"
        f"🔑 <b>API ID :</b>     <code>{u['api_id']}</code>\n"
        f"📋 <b>Topic :</b>      {topic_name}\n"
        + f"🔄 <b>Forwarding:</b>  {fwd_status}\n"
        + f"🔐 <b>Session :</b>    {session_status}\n"
        + (f"📣 <b>Log Channel:</b> {ch_display}\n" if ch_id else "")
        + f"🆔 <b>DB ID :</b>      <code>{u['id']}</code>\n"
        + f"👤 <b>Added by :</b>   <code>{user_id}</code>\n"
        + (
            f"📅 <b>Added on date :</b> {added_at_date}\n"
            f"🕒 <b>Added on time :</b> {added_at_time}\n"
            if u.get("created_at")
            else ""
        )
        + "━━━━━━━━━━━━━━━━━━━━━\n"
        + "🔐 Session stored <b>encrypted</b>."
    )
