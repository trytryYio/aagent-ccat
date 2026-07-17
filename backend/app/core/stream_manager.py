import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

STREAM_TIMEOUT = 300
CLEANUP_INTERVAL = 60


@dataclass
class StreamContext:
    message_id: str
    task: asyncio.Task
    queue: asyncio.Queue
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    # 为断线续传保存已发出事件的快照
    delivered_events: list[tuple[str, dict]] = field(default_factory=list)
    final_payload: dict | None = None


class StreamManager:
    def __init__(self):
        self._streams: dict[str, StreamContext] = {}
        self._cleanup_task: asyncio.Task | None = None

    def register(self, message_id: str, task: asyncio.Task) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._streams[message_id] = StreamContext(
            message_id=message_id,
            task=task,
            queue=queue,
        )
        return queue

    def get(self, message_id: str) -> StreamContext | None:
        ctx = self._streams.get(message_id)
        if ctx:
            ctx.last_active = time.time()
        return ctx

    def cancel(self, message_id: str) -> bool:
        ctx = self._streams.pop(message_id, None)
        if ctx:
            ctx.task.cancel()
            logger.info(f"Stream {message_id} cancelled")
            return True
        return False

    def remove(self, message_id: str):
        self._streams.pop(message_id, None)

    def start_cleanup(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            now = time.time()
            stale = [
                mid for mid, ctx in self._streams.items()
                if now - ctx.last_active > STREAM_TIMEOUT
            ]
            for mid in stale:
                logger.warning(f"Cleaning stale stream {mid}")
                self.cancel(mid)


stream_manager = StreamManager()
