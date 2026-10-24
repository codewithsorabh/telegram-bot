# ─────────────────────────────────────────────
#  engine / worker.py
#
#  Per-account independent forwarding worker.
#
#  Each linked Telegram account gets one AccountWorker.
#  Workers are fully isolated — one FloodWait never
#  blocks another worker.
#
#  Forward method: client.forward_messages()
#  (Preserves original message, caption, media,
#   albums, formatting, and forward tag.)
# ─────────────────────────────────────────────

import asyncio
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    SessionExpiredError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    ChannelPrivateError,
    ChatWriteForbiddenError,
)

from config.logger import LOG
try:
    from worker_queue import WorkerQueue
except ImportError:
    from userbot.worker_queue import WorkerQueue
from models.forwarding_state import upsert_state, get_state
from models.forwarding_schedule import has_been_run, mark_run
from models.users import get_user_by_id
from utils.forward_validator import (
    _forward_validator,
    resolve_telethon_target,
)
from utils.date_formatter import format_ist_time, format_duration
from utils.log_channel import (
    log_success,
    log_failed,
    log_flood,
    log_banned,
    log_unreachable,
    log_topic_fallback,
    notify_owner_expired,
    notify_owner_banned,
)


class AccountWorker:
    """
    Independent forwarding worker for a single Telegram account.

    Architecture:
        Scheduler → AccountWorker.start()
                        └─ asyncio.Task: _run_loop()
                                ├─ Fetch latest message from source channel
                                ├─ Enqueue target URL tasks into WorkerQueue
                                ├─ Process items sequentially: _forward_to()
                                │     └─ client.forward_messages(entity, messages)
                                ├─ Schedule next run: interval_seconds + 5s
                                └─ FloodWait isolation: pause only affected worker
    """

    MAX_RETRIES = 3
    BACKOFF_BASE = 2.0  # exponential backoff multiplier

    def __init__(self, user: dict, redis_client=None):
        self.user = user
        self.uid = user["id"]
        self.r = redis_client
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._queue = WorkerQueue(self.uid)
        self.queue = self._queue
        self._client: TelegramClient | None = None

    # ──────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────
    def start(self) -> asyncio.Task:
        """Start the worker as an asyncio background task."""
        if self._task and not self._task.done():
            return self._task
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name=f"worker-{self.uid}",
        )
        self._task.add_done_callback(self._on_task_done)
        LOG.info(
            "▶️  Worker started for user_id=%s phone=%s",
            self.uid,
            self.user.get("phone_number"),
        )
        self._set_redis("running", "1")
        self._set_redis("status", "running")
        return self._task

    def stop(self) -> None:
        """Signal the worker to stop gracefully after current iteration."""
        self._stop_event.set()
        LOG.info("⏹️  Worker stop signalled for user_id=%s", self.uid)
        self._set_redis("status", "stopped")

    async def stop_async(self) -> None:
        """Signal worker to stop and disconnect Telethon client cleanly."""
        self._stop_event.set()
        if self._client:
            try:
                import inspect
                dis = self._client.disconnect()
                if inspect.isawaitable(dis):
                    await dis
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        LOG.info("⏹️  Worker user_id=%s stopped cleanly", self.uid)
        self._set_redis("status", "stopped")

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ──────────────────────────────────────────
    #  Core loop
    # ──────────────────────────────────────────
    async def _run_loop(self) -> None:
        """Main forwarding loop. Runs until stop() is called or a fatal error occurs."""
        # Connect Telethon client
        try:
            self._client = await self._connect_client()
        except Exception as exc:
            LOG.error("❌ Worker user_id=%s — cannot connect: %s", self.uid, exc)
            self._set_redis("status", "error")
            return

        LOG.info("🔗 Worker user_id=%s connected to Telegram", self.uid)

        # Register @beem auto-verification listener & scan dialogs
        try:
            from userbot.auto_join import register_beem_listener, scan_and_verify_beem_dialogs
            register_beem_listener(self._client, self.uid)
            asyncio.create_task(scan_and_verify_beem_dialogs(self._client, self.uid))
        except Exception as beem_exc:
            LOG.warning("[%s] Could not initialize beem listener: %s", self.uid, beem_exc)

        while not self._stop_event.is_set():
            try:
                # Check and resend pending join requests older than 1000s
                try:
                    from userbot.auto_join import check_and_resend_pending_join_requests
                    await check_and_resend_pending_join_requests(self._client, self.uid, cooldown_seconds=1000)
                except Exception as join_exc:
                    LOG.debug("[%s] Pending join resend check notice: %s", self.uid, join_exc)

                await self._forward_cycle()
            except FloodWaitError as exc:
                wait_secs = exc.seconds + 5  # small buffer
                until = datetime.now(timezone.utc) + timedelta(seconds=wait_secs)
                LOG.warning(
                    "🌊 FloodWait user_id=%s — pausing %ds until %s",
                    self.uid,
                    wait_secs,
                    until.isoformat(),
                )
                self._set_redis("flood_until", str(int(until.timestamp())))
                self._set_redis("status", "flood_wait")
                await asyncio.sleep(wait_secs)
                self._set_redis("status", "running")
                self._del_redis("flood_until")
                continue
            except (
                UserDeactivatedBanError,
                UserDeactivatedError,
                AuthKeyUnregisteredError,
                SessionRevokedError,
                SessionExpiredError,
            ) as exc:
                LOG.error(
                    "💀 Fatal session error user_id=%s: %s — stopping worker",
                    self.uid,
                    exc,
                )
                self._set_redis("status", "session_error")
                try:
                    from models.users import update_user_session_status

                    st = "banned" if isinstance(exc, (UserDeactivatedBanError, UserDeactivatedError)) else "expired"
                    update_user_session_status(self.uid, st, str(exc))
                except Exception as db_exc:
                    LOG.error(
                        "Failed to update session status for user_id=%s in DB: %s",
                        self.uid,
                        db_exc,
                    )
                u_obj = get_user_by_id(self.uid)
                if u_obj:
                    if isinstance(exc, UserDeactivatedBanError):
                        asyncio.create_task(notify_owner_banned(u_obj, str(exc)))
                    else:
                        asyncio.create_task(notify_owner_expired(u_obj, str(exc)))
                break
            except asyncio.CancelledError:
                LOG.info("🛑 Worker user_id=%s — task cancelled", self.uid)
                break
            except Exception as exc:
                LOG.error("⚠️  Worker user_id=%s — unexpected error: %s", self.uid, exc)

            # Cycle pause between scheduler polling iterations
            sleep_secs = 10.0
            LOG.debug("💤 Worker user_id=%s — sleeping %.1fs", self.uid, sleep_secs)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_secs)
            except asyncio.TimeoutError:
                pass  # Normal — timeout means we continue the loop

        await self._disconnect_client()
        LOG.info("✅ Worker user_id=%s stopped cleanly", self.uid)

    # ──────────────────────────────────────────
    #  Forward one cycle
    # ──────────────────────────────────────────
    async def _forward_cycle(self) -> None:
        """Fetch latest message from source channel and forward to all topic destination URLs."""
        topic_key = self.user.get("topic_key") or self.user.get("topic_id")

        if not topic_key:
            LOG.debug(
                "⚠️ Worker user_id=%s — no topic_key assigned to account", self.uid
            )
            return

        from models.topics import get_topics_by_key, get_topic_by_id

        # Resolve numeric topic_id → url_key string (legacy migration path)
        if str(topic_key).isdigit():
            t_obj = get_topic_by_id(int(topic_key))
            if t_obj and t_obj.get("url_key"):
                topic_key = t_obj["url_key"]
            else:
                LOG.warning(
                    "⚠️ Worker user_id=%s — numeric topic_id=%s not found",
                    self.uid,
                    topic_key,
                )
                return

        topic_key = str(topic_key)

        # Load all destination entries for this url_key
        topic_entries = get_topics_by_key(topic_key)
        active_entries = [t for t in topic_entries if t.get("enabled", True)]

        if not active_entries:
            LOG.debug(
                "⚠️ Worker user_id=%s — topic_key '%s' has 0 enabled destination URLs",
                self.uid,
                topic_key,
            )
            return

        msg_id = await self._get_latest_message_id(
            "me"
        )  # Use "me" to fetch from Saved Messages
        if not msg_id:
            return

        # Enqueue one forward task per active destination URL
        for entry in active_entries:
            await self._queue.put(
                {
                    "source_url": "me",
                    "msg_id": msg_id,
                    "dest": entry,
                    "topic_id": entry["id"],
                    "interval_seconds": entry.get("interval_seconds", 60),
                    "url_key": topic_key,
                }
            )

        # Process dequeued tasks sequentially
        while not self._queue.empty():
            item = await self._queue.get()
            try:
                await self._process_queued_item(item)
            finally:
                self._queue.task_done()

    async def _process_queued_item(self, item: dict) -> None:
        source_url = item["source_url"]
        msg_id = item["msg_id"]
        dest = item["dest"]
        topic_id = item["topic_id"]
        interval = item.get("interval_seconds", 60)
        url_key = item.get("url_key", "")

        await self._forward_to(source_url, msg_id, dest, topic_id, url_key)
        if interval > 0:
            await asyncio.sleep(min(interval, 5))  # Smooth throttle

    async def _get_latest_message_id(self, source_url: str) -> int | None:
        """Get the ID of the most recent message in the source channel using GetHistoryRequest."""
        if not self._client:
            LOG.error("❌ Worker user_id=%s — client not connected", self.uid)
            return None

        try:
            from telethon.tl.functions.messages import GetHistoryRequest

            source_entity, _ = await resolve_telethon_target(self._client, source_url)
            saved = await self._client(
                GetHistoryRequest(
                    peer=source_entity,
                    offset_id=0,
                    offset_date=None,
                    add_offset=0,
                    limit=1,
                    max_id=0,
                    min_id=0,
                    hash=0,
                )
            )
            msgs = getattr(saved, "messages", [])
            if msgs:
                msg_id = msgs[0].id
                self._set_redis("last_msg_id", str(msg_id))
                LOG.debug("📩 Latest msg_id=%s from %s", msg_id, source_url)
                return msg_id
        except (
            UsernameNotOccupiedError,
            UsernameInvalidError,
            ChannelPrivateError,
        ) as exc:
            LOG.error("❌ Source %s unreachable: %s", source_url, exc)
        except Exception as exc:
            LOG.error("❌ Failed to fetch from %s: %s", source_url, exc)
        return None

    async def _forward_to(
        self,
        source_url: str,
        msg_id: int,
        dest: dict,
        topic_id: int,
        url_key: str = "",
    ) -> None:
        """Forward a message to a single destination with next_run_at schedule enforcement and retry logic."""
        dest_url = dest.get("url") or dest.get("normalized") or dest.get("raw", "")
        interval = dest.get("interval_seconds", 60)

        now = datetime.now(timezone.utc)
        state = get_state(self.uid, topic_id, dest_url)

        from models.users import get_user_by_id

        u_obj = get_user_by_id(self.uid)
        ch_id = u_obj.get("channel_id") if u_obj else self.user.get("channel_id")

        # 30-second global delay on very first run for this user+url_key+url
        if url_key and not has_been_run(self.uid, url_key, dest_url):
            LOG.info(
                "⏳ [%s] First run for %s — waiting 30s before forwarding…",
                self.uid,
                dest_url,
            )
            await asyncio.sleep(30)

        # Schedule check: if NOW < next_run_at, skip until scheduled time
        if state:
            next_run = state.get("next_run_at")
            if next_run:
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
                if now < next_run:
                    remaining_secs = int((next_run - now).total_seconds())
                    next_run_str = format_ist_time(next_run)
                    LOG.info(
                        "⏰ [%s] Skipping %s — scheduled next_run_at is %s (in %s)",
                        self.uid,
                        dest_url,
                        next_run_str,
                        format_duration(remaining_secs),
                    )
                    # asyncio.create_task(log_skipping(ch_id, self.uid, dest_url, next_run_str, remaining_secs))
                    return

        if not self._client:
            LOG.error("❌ Worker user_id=%s — client not connected", self.uid)
            return

        dest_entity = None
        reply_to_topic = None
        source_entity = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                # Ensure destination channel is auto-joined
                try:
                    from userbot.auto_join import join_channel_target, is_channel_joined
                    if not is_channel_joined(self.uid, dest_url):
                        await join_channel_target(self._client, self.uid, dest_url)
                except Exception as aj_exc:
                    LOG.debug("[%s] Auto-join check notice for %s: %s", self.uid, dest_url, aj_exc)

                dest_entity, reply_to_topic = await resolve_telethon_target(
                    self._client, dest
                )
                source_entity, _ = await resolve_telethon_target(
                    self._client, source_url
                )

                LOG.info(
                    "🚀 [%s] Forwarding msg_id=%s → %s (topic_id=%s, attempt=%d/%d)…",
                    self.uid,
                    msg_id,
                    dest_url,
                    reply_to_topic,
                    attempt,
                    self.MAX_RETRIES,
                )

                await _forward_validator(
                    client=self._client,
                    entity=dest_entity,
                    messages=msg_id,
                    from_peer=source_entity,
                    reply_to=reply_to_topic,
                )

                sent_time = datetime.now(timezone.utc)
                # Next run schedule: interval_seconds + 5 seconds buffer
                next_run_time = sent_time + timedelta(seconds=interval + 5)
                next_run_str = format_ist_time(next_run_time)

                LOG.info(
                    "✅ [%s] Forwarded msg_id=%s → %s (next_run_at=%s) SUCCESSFULLY",
                    self.uid,
                    msg_id,
                    dest_url,
                    next_run_str,
                )
                upsert_state(
                    telegram_user_id=self.uid,
                    topic_id=topic_id,
                    url=dest_url,
                    last_message_id=msg_id,
                    last_sent_at=sent_time,
                    next_run_at=next_run_time,
                    status="success",
                    failure_count=0,
                )
                if url_key:
                    mark_run(self.uid, url_key, dest_url)
                asyncio.create_task(
                    log_success(ch_id, self.uid, dest_url, msg_id, next_run_str)
                )
                return

            except FloodWaitError:
                LOG.warning("🌊 [%s] FloodWait encountered on %s", self.uid, dest_url)
                raise  # Propagate to loop-level handler

            except (
                UsernameNotOccupiedError,
                UsernameInvalidError,
                ChannelPrivateError,
                ChatWriteForbiddenError,
                ValueError,
            ) as exc:
                LOG.error(
                    "❌ [%s] Target channel invalid or unreachable → %s: %s (pausing 10m)",
                    self.uid,
                    dest_url,
                    exc,
                )
                sent_time = datetime.now(timezone.utc)
                upsert_state(
                    telegram_user_id=self.uid,
                    topic_id=topic_id,
                    url=dest_url,
                    status="failed",
                    last_error=str(exc),
                    failure_count=attempt,
                    next_run_at=sent_time + timedelta(minutes=10),
                )
                asyncio.create_task(
                    log_unreachable(ch_id, self.uid, dest_url, str(exc))
                )
                return  # No retry for permanent errors

            except Exception as exc:
                exc_str = str(exc)

                # Permanent ban — mark account status as banned & disable forwarding
                if (
                    "banned from sending messages in supergroups" in exc_str.lower()
                    or "you're banned" in exc_str.lower()
                ):
                    LOG.warning(
                        "🚫 [%s] Account Banned in %s — disabling forwarding",
                        self.uid,
                        dest_url,
                    )
                    sent_time = datetime.now(timezone.utc)
                    upsert_state(
                        telegram_user_id=self.uid,
                        topic_id=topic_id,
                        url=dest_url,
                        status="banned",
                        last_error=exc_str,
                        failure_count=attempt,
                        next_run_at=sent_time + timedelta(days=30),
                    )
                    asyncio.create_task(log_banned(ch_id, self.uid, dest_url))
                    u_obj = get_user_by_id(self.uid)
                    if u_obj:
                        asyncio.create_task(
                            notify_owner_banned(
                                u_obj, f"Banned in {dest_url}: {exc_str}"
                            )
                        )
                    return

                # FloodWait via string — parse seconds and skip URL for that duration
                if (
                    "a wait of" in exc_str.lower()
                    and "seconds is required" in exc_str.lower()
                ):
                    import re as _re

                    _m = _re.search(r"wait of (\d+) seconds", exc_str, _re.IGNORECASE)
                    flood_secs = int(_m.group(1)) if _m else 600
                    LOG.warning(
                        "🌊 [%s] FloodWait %ds on %s — skipping until cooldown",
                        self.uid,
                        flood_secs,
                        dest_url,
                    )
                    sent_time = datetime.now(timezone.utc)
                    upsert_state(
                        telegram_user_id=self.uid,
                        topic_id=topic_id,
                        url=dest_url,
                        status="flood_wait",
                        last_error=exc_str,
                        failure_count=0,
                        next_run_at=sent_time + timedelta(seconds=flood_secs + 10),
                    )
                    # Mark as "seen" so 30s first-run delay won't repeat on restart
                    if url_key:
                        mark_run(self.uid, url_key, dest_url)
                    asyncio.create_task(
                        log_flood(ch_id, self.uid, dest_url, flood_secs)
                    )
                    return

                if "TOPIC_CLOSED" in exc_str:
                    LOG.warning(
                        "⚠️ [%s] Topic %s is closed in %s — searching for an open topic fallback…",
                        self.uid,
                        reply_to_topic,
                        dest_url,
                    )
                    if dest_entity is not None and source_entity is not None and self._client:
                        try:
                            from utils.forward_validator import find_open_forum_topic

                            open_topic_id = await find_open_forum_topic(
                                self._client, dest_entity
                            )
                            await _forward_validator(
                                client=self._client,
                                entity=dest_entity,
                                messages=msg_id,
                                from_peer=source_entity,
                                reply_to=open_topic_id,
                            )
                            sent_time = datetime.now(timezone.utc)
                            next_run_time = sent_time + timedelta(seconds=interval + 5)
                            LOG.info(
                                "✅ [%s] Forwarded msg_id=%s → %s (topic_id=%s fallback) SUCCESSFULLY",
                                self.uid,
                                msg_id,
                                dest_url,
                                open_topic_id,
                            )
                            upsert_state(
                                telegram_user_id=self.uid,
                                topic_id=topic_id,
                                url=dest_url,
                                last_message_id=msg_id,
                                last_sent_at=sent_time,
                                next_run_at=next_run_time,
                                status="success",
                                failure_count=0,
                            )
                            if url_key:
                                mark_run(self.uid, url_key, dest_url)
                            asyncio.create_task(
                                log_topic_fallback(ch_id, self.uid, dest_url, open_topic_id)
                            )
                            return
                        except Exception as fallback_exc:
                            exc = fallback_exc

                LOG.error(
                    "⚠️ [%s] Attempt %d/%d FAILED → %s: %s",
                    self.uid,
                    attempt,
                    self.MAX_RETRIES,
                    dest_url,
                    exc,
                )
                if attempt < self.MAX_RETRIES:
                    backoff = self.BACKOFF_BASE**attempt
                    await asyncio.sleep(backoff)
                else:
                    upsert_state(
                        telegram_user_id=self.uid,
                        topic_id=topic_id,
                        url=dest_url,
                        status="failed",
                        last_error=str(exc),
                        failure_count=attempt,
                    )
                    asyncio.create_task(log_failed(ch_id, self.uid, dest_url, str(exc)))

    # ──────────────────────────────────────────
    #  Telethon client helpers
    # ──────────────────────────────────────────
    async def _connect_client(self) -> TelegramClient:
        session_str = self.user.get("session_string", "")
        api_id = self.user["api_id"]
        api_hash = self.user.get("api_hash") or self.user.get("api_hash_raw", "")

        # A valid Telethon StringSession is always >100 chars.
        # Short/truncated values crash base64 decoding — skip them immediately.
        if not session_str or len(session_str) < 50:
            LOG.error(
                "❌ Worker user_id=%s — session string too short (%d chars), skipping (needs re-auth)",
                self.uid,
                len(session_str) if session_str else 0,
            )
            raise SessionExpiredError(None)

        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            raise SessionExpiredError(None)
        return client

    async def _disconnect_client(self) -> None:
        if self._client:
            try:
                import inspect
                dis = self._client.disconnect()
                if inspect.isawaitable(dis):
                    await dis
            except Exception:
                pass
            self._client = None

    # ──────────────────────────────────────────
    #  Redis helpers
    # ──────────────────────────────────────────
    def _set_redis(self, key: str, value: str, ex: int | None = None) -> None:
        if not self.r:
            return
        try:
            rk = f"worker:{self.uid}:{key}"
            if ex:
                self.r.setex(rk, ex, value)
            else:
                self.r.set(rk, value)
        except Exception:
            pass

    def _del_redis(self, key: str) -> None:
        if not self.r:
            return
        try:
            self.r.delete(f"worker:{self.uid}:{key}")
        except Exception:
            pass

    # ──────────────────────────────────────────
    #  Task callbacks
    # ──────────────────────────────────────────
    def _on_task_done(self, task: asyncio.Task) -> None:
        self._del_redis("running")
        self._set_redis("status", "stopped")
        if not task.cancelled() and task.exception():
            LOG.error("Worker user_id=%s died: %s", self.uid, task.exception())
