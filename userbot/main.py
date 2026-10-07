# ─────────────────────────────────────────────────────────────────────────────
#  userbot / main.py
#
#  Userbot Forwarding & Auto-Join Service Entry Point.
#  Command: python ./userbot/main.py
#
#  Responsibilities:
#    • Bootstrap database tables & migrations
#    • Launch ForwardingScheduler managing AccountWorkers
#    • Handle message forwarding from Saved Messages ("me") to targets
#    • Run Auto-Join with 1000s pending request resend
#    • Run real-time @beem bot verification flow
#    • Listen to Redis signals from Telegram Bot
# ─────────────────────────────────────────────────────────────────────────────

import sys
import os
import asyncio
import signal
import json

# Ensure userbot dir and project root are on sys.path
_USERBOT_DIR = os.path.abspath(os.path.dirname(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_USERBOT_DIR, ".."))
for _p in (_USERBOT_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config.logger import LOG
from config.db import db, r
try:
    from scheduler import ForwardingScheduler
except ImportError:
    from userbot.scheduler import ForwardingScheduler

# ── Database Bootstraps ───────────────────────
from models.config import bootstrap_config_table
from models.topics import bootstrap_topics_table
from models.users import bootstrap_users_table
from models.forwarding_state import bootstrap_forwarding_state_table
from models.forwarding_schedule import bootstrap_forwarding_schedule_table
from models.channel_joins import bootstrap_channel_joins_table


class UserbotForwardingService:
    """
    Independent service coordinating all userbot accounts, forwarding tasks,
    auto-joining, and Redis signal subscriptions.
    """

    def __init__(self):
        self.scheduler: ForwardingScheduler | None = None
        self._stop_event = asyncio.Event()
        self._redis_task: asyncio.Task | None = None
        self.session_checker = None

    def bootstrap_database(self) -> None:
        LOG.info("📦 Bootstrapping database schema for Userbot...")
        bootstrap_config_table()
        bootstrap_topics_table()
        bootstrap_users_table()
        bootstrap_forwarding_state_table()
        bootstrap_forwarding_schedule_table()
        bootstrap_channel_joins_table()
        LOG.info("✅ Database tables verified")

    async def _redis_signal_listener(self) -> None:
        """Listen for start/stop signals published by the Telegram Bot."""
        if not r:
            LOG.info("Redis not configured — worker scheduler will poll PostgreSQL directly.")
            return

        pubsub = r.pubsub()
        try:
            pubsub.subscribe("userbot:signals")
            LOG.info("📡 Subscribed to Redis channel 'userbot:signals'")
            while not self._stop_event.is_set():
                # Non-blocking message check with timeout=0
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=0)
                if msg and msg.get("data"):
                    try:
                        data = json.loads(msg["data"])
                        action = data.get("action")
                        uid = data.get("user_id")
                        if action == "start" and uid and self.scheduler:
                            LOG.info("⚡ Redis signal: START worker user_id=%s", uid)
                            self.scheduler.signal_start(int(uid))
                        elif action == "stop" and uid and self.scheduler:
                            LOG.info("⚡ Redis signal: STOP worker user_id=%s", uid)
                            self.scheduler.signal_stop(int(uid))
                    except Exception as err:
                        LOG.error("Error parsing Redis signal: %s", err)
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            LOG.warning("Redis signal listener stopped: %s", exc)
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    async def run(self) -> None:
        self.bootstrap_database()

        LOG.info("🚀 Starting Userbot Forwarding Service...")
        self.scheduler = ForwardingScheduler(redis_client=r)

        # Launch Redis signal listener
        if r:
            self._redis_task = asyncio.create_task(
                self._redis_signal_listener(),
                name="userbot-redis-listener",
            )

        # Launch scheduler task
        scheduler_task = asyncio.create_task(
            self.scheduler.run_forever(),
            name="userbot-scheduler",
        )

        # Launch 2-Hour Session & Ban/Suspend Checker
        try:
            from session_checker import SessionCheckerWorker
            self.session_checker = SessionCheckerWorker(scheduler=self.scheduler)
            self.session_checker.start()
        except Exception as sc_exc:
            LOG.warning("Failed to start SessionCheckerWorker: %s", sc_exc)

        LOG.info("🟢 Userbot Forwarding Service is running.")

        # Wait until stop event or cancellation
        try:
            await self._stop_event.wait()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await self.shutdown(scheduler_task)

    async def shutdown(self, scheduler_task: asyncio.Task | None = None) -> None:
        LOG.info("🛑 Shutting down Userbot Service...")
        self._stop_event.set()

        if self._redis_task and not self._redis_task.done():
            self._redis_task.cancel()
            try:
                await self._redis_task
            except (asyncio.CancelledError, Exception):
                pass

        if self.session_checker:
            try:
                await self.session_checker.stop()
            except Exception:
                pass

        if self.scheduler:
            try:
                await self.scheduler.stop_async()
            except Exception:
                pass

        if scheduler_task and not scheduler_task.done():
            scheduler_task.cancel()
            try:
                await scheduler_task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            if db and not db.closed:
                db.close()
                LOG.info("PostgreSQL connection closed")
        except Exception as exc:
            LOG.error("Error closing DB: %s", exc)

        try:
            if r:
                r.close()
                LOG.info("Redis connection closed")
        except Exception as exc:
            LOG.error("Error closing Redis: %s", exc)

        LOG.info("👋 Userbot Service shutdown complete.")


def main():
    service = UserbotForwardingService()
    try:
        asyncio.run(service.run())
    except (KeyboardInterrupt, SystemExit):
        LOG.info("👋 Userbot Service stopped by user.")


if __name__ == "__main__":
    main()
