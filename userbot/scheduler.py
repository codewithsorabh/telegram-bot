# ─────────────────────────────────────────────
#  engine / scheduler.py
#
#  Manages per-account forwarding workers.
#
#  The Scheduler ONLY manages workers.
#  It NEVER forwards messages itself.
#
#  Logic per tick:
#   1. Load all active users from DB
#   2. For each user:
#      a. Plan expired?        → skip (stop worker if running)
#      b. forwarding_enabled?  → skip if False (stop worker if running)
#      c. Worker running?      → skip if True
#      d. Start AccountWorker
#   3. Stop workers for users no longer in active list
# ─────────────────────────────────────────────

import asyncio
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config.logger import LOG

if TYPE_CHECKING:
    try:
        from worker import AccountWorker
    except ImportError:
        from userbot.worker import AccountWorker


class ForwardingScheduler:
    """
    Central scheduler that manages one AccountWorker per active
    Telegram account.

    Usage (in main.py):
        scheduler = ForwardingScheduler(redis_client=r)
        await scheduler.run_forever()
    """

    def __init__(self, redis_client=None):
        self.r = redis_client
        self._workers: dict[int, "AccountWorker"] = {}
        self._interval = int(os.getenv("SCHEDULER_INTERVAL", "60"))
        self._max_workers = int(os.getenv("MAX_CONCURRENT_WORKERS", "2000"))
        self._running = True
        self._stop_event = asyncio.Event()

    # ──────────────────────────────────────────
    #  Main loop
    # ──────────────────────────────────────────
    async def run_forever(self) -> None:
        """Run the scheduler indefinitely. Call as asyncio task."""
        LOG.info(
            "📅 Scheduler started (interval=%ds, max_workers=%d)",
            self._interval,
            self._max_workers,
        )
        self._stop_event.clear()
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                LOG.error("Scheduler tick error: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
                break  # stop event set cleanly
            except asyncio.TimeoutError:
                pass  # normal — continue ticking

        await self._stop_all_workers()
        LOG.info("📅 Scheduler stopped cleanly")

    def stop(self) -> None:
        """Signal the scheduler to stop after current tick."""
        self._running = False
        self._stop_event.set()

    async def stop_async(self) -> None:
        """Stop scheduler and wait for workers to terminate."""
        self._running = False
        self._stop_event.set()
        await self._stop_all_workers()

    # ──────────────────────────────────────────
    #  External signals from bot handlers
    # ──────────────────────────────────────────
    def signal_stop(self, user_id: int) -> None:
        """Stop worker for a specific user (e.g., admin toggled forwarding off)."""
        worker = self._workers.get(user_id)
        if worker and worker.is_running():
            worker.stop()
            LOG.info("Scheduler: stopped worker for user_id=%s on demand", user_id)

    def signal_start(self, user_id: int) -> None:
        """Hint the scheduler to start a worker on next tick for user_id."""
        LOG.info("Scheduler: start hint received for user_id=%s", user_id)
        # Will be picked up on next _tick()

    def is_worker_running(self, user_id: int) -> bool:
        w = self._workers.get(user_id)
        return w is not None and w.is_running()

    # ──────────────────────────────────────────
    #  Tick logic
    # ──────────────────────────────────────────
    async def _tick(self) -> None:
        from models.users import get_active_users
        try:
            from worker import AccountWorker
        except ImportError:
            from userbot.worker import AccountWorker

        active_users = get_active_users()
        active_ids = {u["id"] for u in active_users}

        # Stop workers for users no longer active
        for uid in list(self._workers.keys()):
            if uid not in active_ids:
                self._stop_worker(uid)

        running_count = sum(1 for w in self._workers.values() if w.is_running())

        # Start workers for active users without one (up to max_workers)
        for user in active_users:
            if running_count >= self._max_workers:
                LOG.warning(
                    "Scheduler: max worker limit reached (%d) — remaining accounts queued",
                    self._max_workers,
                )
                break

            uid = user["id"]

            # Check plan expiry
            expired_at = user.get("plan_expired_at")
            if expired_at and expired_at < datetime.now(timezone.utc):
                LOG.debug("Scheduler: user_id=%s plan expired — skipping", uid)
                self._stop_worker(uid)
                continue

            # Check forwarding enabled
            if not user.get("forwarding_enabled", True):
                self._stop_worker(uid)
                continue

            # Check if already running
            existing = self._workers.get(uid)
            if existing and existing.is_running():
                continue

            # Start new worker
            worker = AccountWorker(user, redis_client=self.r)
            self._workers[uid] = worker
            worker.start()
            running_count += 1
            LOG.info(
                "Scheduler: started worker for user_id=%s (%d/%d)",
                uid,
                running_count,
                self._max_workers,
            )
            await asyncio.sleep(0.05)  # Stagger startup to prevent CPU/IO spikes

    # ──────────────────────────────────────────
    #  Worker lifecycle helpers
    # ──────────────────────────────────────────
    def _stop_worker(self, user_id: int) -> None:
        worker = self._workers.get(user_id)
        if worker and worker.is_running():
            worker.stop()
        self._workers.pop(user_id, None)

    async def _stop_all_workers(self) -> None:
        LOG.info("Scheduler: stopping %d worker(s)…", len(self._workers))
        for uid, worker in list(self._workers.items()):
            try:
                await worker.stop_async()
            except Exception:
                pass
        self._workers.clear()

    async def _wait_stop(self) -> None:
        """Coroutine that resolves when _running is False."""
        while self._running:
            await asyncio.sleep(0.5)
