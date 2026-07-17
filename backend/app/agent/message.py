"""Agent 消息定义：跨 Agent 通信的消息格式与序列化。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Literal

# 预定义 action 类型
Action = Literal["invoke", "result", "error", "stream"]


@dataclass
class AgentMessage:
    """Agent 间通信消息。"""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    from_agent: str = ""
    to_agent: str = ""
    task_id: str = ""
    action: Action = "invoke"
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> AgentMessage:
        """从 JSON 字符串反序列化。"""
        obj = json.loads(data)
        return cls(**obj)

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return asdict(self)

    @classmethod
    def create_invoke(
        cls,
        from_agent: str,
        to_agent: str,
        task_id: str,
        payload: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """创建 invoke 消息。"""
        return cls(
            from_agent=from_agent,
            to_agent=to_agent,
            task_id=task_id,
            action="invoke",
            payload=payload or {},
        )

    @classmethod
    def create_result(
        cls,
        from_agent: str,
        to_agent: str,
        task_id: str,
        payload: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """创建 result 消息。"""
        return cls(
            from_agent=from_agent,
            to_agent=to_agent,
            task_id=task_id,
            action="result",
            payload=payload or {},
        )

    @classmethod
    def create_error(
        cls,
        from_agent: str,
        to_agent: str,
        task_id: str,
        error: str,
    ) -> AgentMessage:
        """创建 error 消息。"""
        return cls(
            from_agent=from_agent,
            to_agent=to_agent,
            task_id=task_id,
            action="error",
            payload={"error": error},
        )
