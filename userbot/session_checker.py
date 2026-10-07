# ─────────────────────────────────────────────────────────────────────────────
#  userbot / session_checker.py
#
#  Periodic 2-Hour User Session & Ban/Suspend Checker.
#  Runs in the background every 2 hours (7200s).
#  Validates Telethon StringSessions, detects account bans, suspensions,
#  and expirations, and updates the database directly.
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    UserDeactivatedError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    SessionExpiredError,
)

from config.logger import LOG
from models.users import (
    get_all_users,
    update_user_session_status,
)


async def validate_single_account(user: dict) -> str:
    """
    Validates a single user's Telethon session.
    Updates the database with the result ('active', 'banned', 'suspended', 'expired').
    """
    uid = user.get("id")
    api_id = user.get("api_id")
    api_hash = user.get("api_hash") or user.get("api_hash_raw", "")
    session_string = user.get("session_string", "")
    phone = user.get("phone_number") or user.get("phone") or "unknown"

    if not api_id or not api_hash or not session_string or len(session_string) < 50:
        LOG.warning("[%s] Session invalid/too short for phone=%s -> marking expired", uid, phone)
        update_user_session_status(uid, "expired", "Session string missing or too short")
        return "expired"

    client = None
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            LOG.warning("[%s] Telethon client not authorized for phone=%s -> expired", uid, phone)
            update_user_session_status(uid, "expired", "Session not authorized")
            return "expired"

        # Session is valid and active
        update_user_session_status(uid, "active", None)
        LOG.info("✅ [%s] Session active & verified for phone=%s", uid, phone)
        return "active"

    except (UserDeactivatedBanError, UserDeactivatedError) as exc:
        err_msg = str(exc)
        LOG.error("🚫 [%s] Account BANNED/DEACTIVATED for phone=%s: %s", uid, phone, err_msg)
        update_user_session_status(uid, "banned", err_msg)
        try:
            from utils.log_channel import notify_owner_banned
            asyncio.create_task(notify_owner_banned(user, f"Account banned: {err_msg}"))
        except Exception:
            pass
        return "banned"

    except (AuthKeyUnregisteredError, SessionRevokedError, SessionExpiredError) as exc:
        err_msg = str(exc)
        LOG.error("❌ [%s] Session EXPIRED/REVOKED for phone=%s: %s", uid, phone, err_msg)
        update_user_session_status(uid, "expired", err_msg)
        try:
            from utils.log_channel import notify_owner_expired
            asyncio.create_task(notify_owner_expired(user, f"Session expired: {err_msg}"))
        except Exception:
            pass
        return "expired"

    except Exception as exc:
        err_msg = str(exc)
        if "frozen" in err_msg.lower() or "suspend" in err_msg.lower():
            LOG.error("⚠️ [%s] Account SUSPENDED for phone=%s: %s", uid, phone, err_msg)
            update_user_session_status(uid, "suspended", err_msg)
            return "suspended"

        LOG.warning("[%s] Unexpected error during session check for phone=%s: %s", uid, phone, err_msg)
        return user.get("session_status", "active")

    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


async def run_session_checker_cycle(scheduler=None) -> dict:
    """
    Run one validation cycle across all accounts in the database.
    Staggers checks to avoid Telegram rate limits.
    """
    users = get_all_users()
    LOG.info("🔍 Starting 2-hour session check cycle for %d account(s)...", len(users))

    counts = {"active": 0, "banned": 0, "suspended": 0, "expired": 0, "total": len(users)}

    for user in users:
        uid = user.get("id")
        try:
            status = await validate_single_account(user)
            counts[status] = counts.get(status, 0) + 1

            # If account became inactive, stop any active worker
            if status in ("banned", "expired", "suspended") and scheduler:
                scheduler.signal_stop(uid)

            # Stagger connections
            await asyncio.sleep(2.0)
        except Exception as exc:
            LOG.error("[%s] Error checking account: %s", uid, exc)

    LOG.info(
        "🏁 Session check cycle completed: active=%d, expired=%d, banned=%d, suspended=%d (total=%d)",
        counts["active"],
        counts["expired"],
        counts["banned"],
        counts["suspended"],
        counts["total"],
    )
    return counts


class SessionCheckerWorker:
    """
    Background worker that executes run_session_checker_cycle every 2 hours.
    """

    INTERVAL = 7200  # 2 hours in seconds

    def __init__(self, scheduler=None):
        self.scheduler = scheduler
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Task:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="session-checker-worker")
        LOG.info("⏱️ 2-Hour Session Checker scheduled (interval=%ds)", self.INTERVAL)
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        LOG.info("⏱️ Session Checker stopped")

    async def _run_loop(self) -> None:
        # Initial grace delay so workers can start first
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
            return
        except asyncio.TimeoutError:
            pass

        while not self._stop_event.is_set():
            try:
                await run_session_checker_cycle(self.scheduler)
            except Exception as exc:
                LOG.error("Session checker cycle error: %s", exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.INTERVAL)
                break
            except asyncio.TimeoutError:
                pass
