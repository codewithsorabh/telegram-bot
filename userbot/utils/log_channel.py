# ─────────────────────────────────────────────
#  utils / log_channel.py
#
#  Sends formatted forwarding log messages to a
#  Telegram log channel via the Bot HTTP API.
#  Uses httpx async so it never blocks the worker.
# ─────────────────────────────────────────────

import os
import re
import httpx
from dotenv import load_dotenv
from config.logger import LOG

load_dotenv()

from models.users import normalize_channel_id

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


# ── Extract clean @username or channel name from URL ──────────────
def _short_name(url: str) -> str:
    """Return @username or channel_id extracted from a Telegram URL."""
    url = url.strip().rstrip("/")
    # Private channel link: https://t.me/c/1234567890/42 → c/1234567890
    m = re.search(r"t\.me/c/(\d+)", url)
    if m:
        return f"c/{m.group(1)}"
    # Username link: https://t.me/sectormarket/24 → @sectormarket
    m = re.search(r"t\.me/([A-Za-z0-9_]+)", url)
    if m:
        return f"@{m.group(1)}"
    return url


# ── Send a message to a specific user's log channel ───────────────
async def send_log(channel_id: int | str | None, text: str) -> None:
    """Send a plain text message to the specified channel_id."""
    if not _BOT_TOKEN or not channel_id:
        return

    target_chat = normalize_channel_id(channel_id)
    if not target_chat:
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.post(
                f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": target_chat,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if res.status_code != 200:
                LOG.error(
                    "❌ Log channel send to %s failed (%s): %s",
                    target_chat,
                    res.status_code,
                    res.text,
                )
    except Exception as exc:
        LOG.error("❌ Log channel send to %s exception: %s", target_chat, exc)


# ── Convenience formatters ────────────────────────────────────────
async def log_success(
    channel_id: int | str | None, uid: int, dest_url: str, msg_id: int, next_run: str
) -> None:
    name = _short_name(dest_url)
    await send_log(
        channel_id,
        f" ✔  {name} forwarded Successfully \n\n👉🏻<code>{uid}</code> Next run: {next_run}",
    )


async def log_failed(
    channel_id: int | str | None, uid: int, dest_url: str, error: str
) -> None:
    name = _short_name(dest_url)
    await send_log(
        channel_id,
        f"✖ {name} FAILED\n\n👉🏻 <code>{uid}</code> <code>{error[:300]}</code>",
    )


from utils.date_formatter import format_duration


async def log_flood(
    channel_id: int | str | None, uid: int, dest_url: str, secs: int
) -> None:
    name = _short_name(dest_url)
    dur_str = format_duration(secs)
    await send_log(
        channel_id,
        f"🕒 {name} \n\n👉🏻 <code>{uid}</code> FloodWait {dur_str} Skipping until cooldown",
    )


async def log_banned(channel_id: int | str | None, uid: int, dest_url: str) -> None:
    name = _short_name(dest_url)
    await send_log(
        channel_id,
        f"👋⏱ {name} — <b>BANNED</b>\n\n 👉🏻<code>{uid}</code> Skipped for 1 hours",
    )


async def log_skipping(
    channel_id: int | str | None,
    uid: int,
    dest_url: str,
    next_run: str,
    remaining_secs: int,
) -> None:
    name = _short_name(dest_url)
    dur_str = format_duration(remaining_secs)
    await send_log(
        channel_id,
        f"⏳ {name} — <b>Skipping</b> \n\n 👉🏻<code>{uid}</code>"
        f"Next run in {dur_str} ({next_run})",
    )


async def log_topic_fallback(
    channel_id: int | str | None, uid: int, dest_url: str, fallback_topic_id
) -> None:
    name = _short_name(dest_url)
    await send_log(
        channel_id,
        f"❌ {name} — topic closed \n\n 👉🏻<code>{uid}</code> Forwarded to fallback topic_id={fallback_topic_id}",
    )


def _clean_error_msg(error: str, dest_url: str = "") -> str:
    err_str = str(error)
    if "Cannot find any entity" in err_str or "Cannot find Telegram entity" in err_str:
        entity_name = dest_url.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip()
        if not entity_name:
            import re
            m = re.search(r"['\"]([^'\"]+)['\"]", err_str)
            entity_name = m.group(1) if m else "chat"
        return f"Cannot find Telegram entity for '{entity_name}' — please check link or ensure account has joined."
    return err_str


async def log_unreachable(
    channel_id: int | str | None, uid: int, dest_url: str, error: str
) -> None:
    if "You can't write in this chat" in error:
        return

    name = _short_name(dest_url)
    clean_err = _clean_error_msg(error, dest_url)
    await send_log(
        channel_id,
        f"⏸ {name} — unreachable (paused 10m)\n\n 👉🏻 <code>{uid}</code> <code>{clean_err[:300]}</code>",
    )


async def log_warning(channel_id: int | str | None, uid: int, message: str) -> None:
    await send_log(channel_id, f"‼️ <code>{uid}</code> <b>Warning:</b> \n\n {message}")


async def log_error(channel_id: int | str | None, uid: int, message: str) -> None:
    await send_log(channel_id, f"⛔ <code>{uid}</code> <b>Error:</b> \n\n {message}")


# ── Owner Log Channel Notifications ─────────────────────────────
async def send_owner_log(text: str) -> None:
    """Send log event to the system Owner Log Channel if configured."""
    from models.config import get_owner_log_channel

    owner_ch = get_owner_log_channel()
    if not owner_ch:
        return
    await send_log(owner_ch, text)


async def notify_owner_expired(user: dict, reason: str = "") -> None:
    """Notify Owner Log Channel when a user account session expires/revokes."""
    phone = user.get("phone_number") or user.get("phone") or "Unknown"
    uid = user.get("id") or "Unknown"
    added_by = user.get("user_id") or user.get("added_by") or "Unknown"
    text = (
        "⚠️ <b>[OWNER ALERT] Account Session Expired!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Phone:</b> <code>{phone}</code>\n"
        f"🆔 <b>Account ID:</b> <code>{uid}</code>\n"
        f"👤 <b>Added By:</b> <code>{added_by}</code>\n"
        f"❓ <b>Error:</b> <code>{reason[:250]}</code>\n\n"
        "👉 Account requires session re-authentication."
    )
    await send_owner_log(text)


async def notify_owner_banned(user: dict, reason: str = "") -> None:
    """Notify Owner Log Channel when a user account is banned."""
    phone = user.get("phone_number") or user.get("phone") or "Unknown"
    uid = user.get("id") or "Unknown"
    added_by = user.get("user_id") or user.get("added_by") or "Unknown"
    text = (
        "🚫 <b>[OWNER ALERT] Account Banned!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Phone:</b> <code>{phone}</code>\n"
        f"🆔 <b>Account ID:</b> <code>{uid}</code>\n"
        f"👤 <b>Added By:</b> <code>{added_by}</code>\n"
        f"❓ <b>Error:</b> <code>{reason[:350]}</code>\n\n"
        "👉 Account has been marked as banned."
    )
    await send_owner_log(text)
