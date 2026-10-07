# ─────────────────────────────────────────────
#  engine / queue.py
#
#  Per-account asyncio queue for serializing
#  forward requests and preventing API bursts.
# ─────────────────────────────────────────────

import asyncio
from config.logger import LOG


class WorkerQueue:
    """
    Thin wrapper around asyncio.Queue.

    Each AccountWorker gets one WorkerQueue instance.
    Messages are forwarded one-at-a-time in order,
    preventing rapid-fire API calls.
    """

    def __init__(self, user_id: int, maxsize: int = 100):
        self.user_id = user_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: dict) -> None:
        """Enqueue a forward task dict."""
        await self._queue.put(item)
        LOG.debug("Queued task for user_id=%s | qsize=%d", self.user_id, self._queue.qsize())

    async def get(self) -> dict:
        """Block until a task is available, then return it."""
        return await self._queue.get()

    def task_done(self) -> None:
        """Mark most recent dequeued task as complete."""
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()
