"""跨 Agent 共享状态管理：基于 Redis 的 Pub/Sub + Hash 状态共享。"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)


class SharedStateManager:
    """基于 Redis 的跨 Agent 共享状态管理器。

    Redis key 规范：
    - multiagent:state:{task_id}  — Hash，存储各 agent 的结果
    - multiagent:event:{task_id}  — Pub/Sub channel，事件通知
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or self._build_redis_url()
        self._redis: aioredis.Redis | None = None

    @staticmethod
    def _build_redis_url() -> str:
        """从 settings 构建 Redis URL。"""
        auth = ""
        if settings.redis_password:
            auth = f":{settings.redis_password}@"
        return f"redis://{auth}{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"

    async def _get_redis(self) -> aioredis.Redis:
        """获取或创建 Redis 连接（延迟初始化）。"""
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
            )
        return self._redis

    def _state_key(self, task_id: str) -> str:
        return f"multiagent:state:{task_id}"

    def _event_channel(self, task_id: str) -> str:
        return f"multiagent:event:{task_id}"

    async def set_agent_result(
        self, task_id: str, agent_name: str, result: Any
    ) -> None:
        """将指定 agent 的结果写入共享状态，并发布事件通知。"""
        r = await self._get_redis()
        key = self._state_key(task_id)
        value = json.dumps(result, ensure_ascii=False, default=str)
        await r.hset(key, agent_name, value)
        await r.publish(self._event_channel(task_id), json.dumps({
            "agent": agent_name,
            "action": "result",
        }))

    async def get_agent_result(self, task_id: str, agent_name: str) -> Any | None:
        """获取指定 agent 的结果。"""
        r = await self._get_redis()
        key = self._state_key(task_id)
        value = await r.hget(key, agent_name)
        if value is None:
            return None
        return json.loads(value)

    async def get_state(self, task_id: str) -> dict[str, Any]:
        """获取任务的所有 agent 结果。"""
        r = await self._get_redis()
        key = self._state_key(task_id)
        raw = await r.hgetall(key)
        return {k: json.loads(v) for k, v in raw.items()}

    async def wait_for_agents(
        self, task_id: str, agent_names: list[str], timeout: float = 30.0
    ) -> dict[str, Any]:
        """等待指定所有 agent 完成（通过 Pub/Sub 事件通知）。

        返回所有已完成 agent 的结果。超时后返回当前已收集的结果。
        """
        r = await self._get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(self._event_channel(task_id))

        required = set(agent_names)
        collected: dict[str, Any] = {}

        # 先检查已有结果
        existing = await self.get_state(task_id)
        for name in list(required):
            if name in existing:
                collected[name] = existing[name]
                required.discard(name)

        # 等待剩余 agent
        import asyncio
        try:
            async with asyncio.timeout(timeout):
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    data = json.loads(message["data"])
                    agent = data.get("agent")
                    if agent in required:
                        result = await self.get_agent_result(task_id, agent)
                        if result is not None:
                            collected[agent] = result
                            required.discard(agent)
                    if not required:
                        break
        except asyncio.TimeoutError:
            logger.warning(
                f"等待 agent 超时（task={task_id}），"
                f"已完成: {list(collected.keys())}，"
                f"缺失: {list(required)}"
            )
        finally:
            await pubsub.unsubscribe(self._event_channel(task_id))
            await pubsub.close()

        return collected

    async def close(self) -> None:
        """关闭 Redis 连接。"""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
