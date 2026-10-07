# ─────────────────────────────────────────────
#  utils / auto_delete.py
#
#  Central helpers for scheduling automatic
#  message deletion via PTB's JobQueue.
# ─────────────────────────────────────────────

import os
from telegram.ext import ContextTypes
from dotenv import load_dotenv
from config.logger import LOG, log

load_dotenv()

# ── Configurable delay & toggle ─────────────
_raw_enable = os.getenv("AUTO_DELETE", "true").strip().lower()
AUTO_DELETE_ENABLED: bool = _raw_enable not in ("false", "0", "no", "off")

_raw_delay = os.getenv("AUTO_DELETE_TIME", "60")
try:
    AUTO_DELETE_SECS: int = int(_raw_delay)
except ValueError:
    AUTO_DELETE_SECS: int = 60


# ─────────────────────────────────────────────
#  Job name helper  (must be unique per message)
# ─────────────────────────────────────────────
def _job_name(chat_id: int, message_id: int) -> str:
    return f"autodel_{chat_id}_{message_id}"


# ─────────────────────────────────────────────
#  PTB job callback  (runs after the delay)
# ─────────────────────────────────────────────
async def _delete_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently delete the scheduled message."""
    data = context.job.data
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
        )
        LOG.debug("Auto-deleted msg %s in chat %s", data["message_id"], data["chat_id"])
    except Exception as exc:
        LOG.debug("Auto-delete silenced: %s", exc)


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────
def schedule_delete(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    delay: int | float | None = None,
) -> None:
    """
    Schedule `message_id` in `chat_id` for deletion after `delay` seconds.
    Resets any existing timer for this message.
    """
    if not AUTO_DELETE_ENABLED:
        return

    if delay is None:
        delay = AUTO_DELETE_SECS

    if isinstance(delay, str):
        try:
            delay = int(delay)
        except ValueError:
            delay = 60

    if context.job_queue is None:
        LOG.warning("job_queue is None — auto-delete disabled.")
        return

    name = _job_name(chat_id, message_id)

    # Cancel any existing job for this message
    for old_job in context.job_queue.get_jobs_by_name(name):
        old_job.schedule_removal()

    context.job_queue.run_once(
        _delete_job,
        when=delay,
        data={"chat_id": chat_id, "message_id": message_id},
        name=name,
    )


def cancel_delete(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
) -> None:
    """Cancel a pending auto-delete job."""
    if context.job_queue is None:
        return
    name = _job_name(chat_id, message_id)
    for job in context.job_queue.get_jobs_by_name(name):
        job.schedule_removal()
