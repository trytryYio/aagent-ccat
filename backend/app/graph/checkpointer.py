"""Redis-backed LangGraph checkpointer。

替换默认的 InMemorySaver/MemorySaver，使 Agent state 按 thread_id 持久化到 Redis，
支持多副本共享会话与故障转移。

设计取舍：
- 每个 checkpoint 完整序列化后存入 Redis（含 channel_values），不单独拆 blob，
  实现简单且满足 PoC 规模。
- 异步方法直接调用同步方法（LangGraph 的 InMemorySaver 也是这种模式），
  Redis 操作足够快，不会阻塞事件循环。
- Redis 不可用时抛出异常，由调用方决定降级策略；上线前可再包一层 fallback 到内存。
"""

import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime, timezone
from typing import Any, Optional

import redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import InMemorySaver

from app.config import settings

logger = logging.getLogger(__name__)


def build_checkpointer():
    """构建 Redis checkpointer；Redis 不可用时回退到内存。"""
    if not settings.redis_enabled:
        logger.info("Redis checkpointer 已禁用，使用 InMemorySaver")
        return InMemorySaver()

    try:
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=False,  # 需要 bytes
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        return RedisSaver(client, prefix=settings.redis_key_prefix)
    except Exception as e:
        logger.warning("Redis checkpointer 初始化失败，回退到 InMemorySaver: %s", e)
        return InMemorySaver()


class RedisSaver(BaseCheckpointSaver):
    """基于 Redis 的 LangGraph checkpoint 持久化实现。"""

    def __init__(
        self,
        redis_client: redis.Redis,
        *,
        prefix: str = "agent",
        ttl: int = 7 * 24 * 3600,
    ) -> None:
        super().__init__()
        self._redis = redis_client
        self._prefix = prefix
        self._ttl = ttl
        try:
            self._redis.ping()
            logger.info("RedisSaver 初始化成功 (prefix=%s)", prefix)
        except Exception as e:
            logger.error("RedisSaver 初始化失败: %s", e)
            raise

    # ---- key 命名 ----
    def _checkpoint_key(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        return f"{self._prefix}:cp:{thread_id}:{checkpoint_ns}:{checkpoint_id}"

    def _writes_key(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        return f"{self._prefix}:writes:{thread_id}:{checkpoint_ns}:{checkpoint_id}"

    def _index_key(self, thread_id: str, checkpoint_ns: str) -> str:
        return f"{self._prefix}:idx:{thread_id}:{checkpoint_ns}"

    # ---- 序列化辅助 ----
    def _dumps(self, obj: Any) -> bytes:
        type_, data = self.serde.dumps_typed(obj)
        # 用 type 前缀 + NUL + data 的简单格式，便于反序列化
        return type_.encode("utf-8") + b"\x00" + data

    def _loads(self, raw: bytes) -> Any:
        type_bytes, data = raw.split(b"\x00", 1)
        return self.serde.loads_typed((type_bytes.decode("utf-8"), data))

    # ---- 单条 checkpoint 读写 ----
    def _load_checkpoint_tuple(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> Optional[CheckpointTuple]:
        cp_key = self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
        raw = self._redis.get(cp_key)
        if raw is None:
            return None

        try:
            stored = self._loads(raw)
            checkpoint: Checkpoint = stored["checkpoint"]
            metadata: CheckpointMetadata = stored["metadata"]
            parent_id: Optional[str] = stored.get("parent_id")
        except Exception as e:
            logger.error("checkpoint 反序列化失败 %s: %s", cp_key, e)
            return None

        # 加载 writes
        writes: list[PendingWrite] = []
        writes_key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id)
        try:
            for item_raw in self._redis.lrange(writes_key, 0, -1):
                w = self._loads(item_raw)
                # 存储格式: (task_id, channel, value, task_path)
                task_id, channel, value, task_path = w
                writes.append((task_id, channel, value))
        except Exception as e:
            logger.warning("writes 读取失败 %s: %s", writes_key, e)

        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

        parent_config: Optional[RunnableConfig] = None
        if parent_id:
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_id,
                }
            }

        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=writes,
        )

    def _latest_checkpoint_id(
        self, thread_id: str, checkpoint_ns: str
    ) -> Optional[str]:
        ids = self._redis.zrevrange(
            self._index_key(thread_id, checkpoint_ns), 0, 0
        )
        if not ids:
            return None
        latest = ids[0]
        return latest.decode("utf-8") if isinstance(latest, bytes) else latest

    # ---- 必须实现的接口 ----
    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")

        if checkpoint_id := get_checkpoint_id(config):
            return self._load_checkpoint_tuple(thread_id, checkpoint_ns, checkpoint_id)

        latest_id = self._latest_checkpoint_id(thread_id, checkpoint_ns)
        if not latest_id:
            return None
        return self._load_checkpoint_tuple(thread_id, checkpoint_ns, latest_id)

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            # 未实现全局 list，本项目不需要
            return iter([])

        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        config_checkpoint_id = get_checkpoint_id(config)
        before_id = get_checkpoint_id(before) if before else None

        index_key = self._index_key(thread_id, checkpoint_ns)
        # 从最新到最旧遍历
        all_ids = self._redis.zrevrange(index_key, 0, -1)

        count = 0
        for cp_id in all_ids:
            if config_checkpoint_id and cp_id != config_checkpoint_id:
                continue
            if before_id and cp_id >= before_id:
                continue

            tup = self._load_checkpoint_tuple(thread_id, checkpoint_ns, cp_id)
            if tup is None:
                continue

            if filter:
                if not all(
                    tup.metadata.get(k) == v for k, v in filter.items()
                ):
                    continue

            yield tup
            count += 1
            if limit is not None and count >= limit:
                break

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id: str = checkpoint["id"]
        parent_id: Optional[str] = config["configurable"].get("checkpoint_id")

        # 完整保存 checkpoint（含 channel_values），简化实现
        stored = {
            "checkpoint": checkpoint,
            "metadata": get_checkpoint_metadata(config, metadata),
            "parent_id": parent_id,
        }

        cp_key = self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
        pipe = self._redis.pipeline()
        pipe.setex(cp_key, self._ttl, self._dumps(stored))

        # 用时间戳作为 score 加入索引，便于按时间排序
        ts = checkpoint.get("ts") or datetime.now(timezone.utc).isoformat()
        try:
            score = datetime.fromisoformat(ts).timestamp()
        except ValueError:
            score = datetime.now(timezone.utc).timestamp()
        pipe.zadd(self._index_key(thread_id, checkpoint_ns), {checkpoint_id: score})
        pipe.expire(self._index_key(thread_id, checkpoint_ns), self._ttl)
        pipe.execute()

        logger.debug(
            "checkpoint 已保存 %s:%s:%s", thread_id, checkpoint_ns, checkpoint_id
        )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id: str = config["configurable"]["checkpoint_id"]

        writes_key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id)
        for channel, value in writes:
            item = (task_id, channel, value, task_path)
            self._redis.rpush(writes_key, self._dumps(item))
        self._redis.expire(writes_key, self._ttl)

    def delete_thread(self, thread_id: str) -> None:
        # 遍历并删除该 thread 下的所有 checkpoint / writes / index
        pattern_cp = f"{self._prefix}:cp:{thread_id}:*"
        pattern_writes = f"{self._prefix}:writes:{thread_id}:*"
        pattern_idx = f"{self._prefix}:idx:{thread_id}:*"
        for key in self._redis.scan_iter(match=pattern_cp):
            self._redis.delete(key)
        for key in self._redis.scan_iter(match=pattern_writes):
            self._redis.delete(key)
        for key in self._redis.scan_iter(match=pattern_idx):
            self._redis.delete(key)

    # ---- 异步包装：沿用 InMemorySaver 的同步实现 + async 包装 ----
    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return self.get_tuple(config)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        return self.delete_thread(thread_id)
