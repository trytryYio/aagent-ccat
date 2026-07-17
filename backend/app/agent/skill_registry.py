"""技能注册中心：注册技能 → 输出 OpenAI function calling 格式 → 执行技能。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# 技能实现可以是同步或异步函数
SkillImpl = Callable[..., Awaitable[Any]] | Callable[..., Any]


@dataclass
class Skill:
    """表示一个可注册的技能。"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema 格式的参数定义
    impl: SkillImpl = field(repr=False)  # 技能实现函数

    def to_openai_function(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class SkillRegistry:
    """技能注册中心：管理技能的注册、查询和调用。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """注册一个技能。如果同名技能已存在，则覆盖。"""
        if skill.name in self._skills:
            logger.warning(f"技能 '{skill.name}' 已存在，将被覆盖")
        self._skills[skill.name] = skill
        logger.info(f"技能已注册: {skill.name}")

    def get(self, name: str) -> Skill | None:
        """按名称获取技能。"""
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        """返回所有已注册技能。"""
        return list(self._skills.values())

    def to_openai_functions(self) -> list[dict[str, Any]]:
        """将所有已注册技能转为 OpenAI function calling 格式列表。"""
        return [skill.to_openai_function() for skill in self._skills.values()]

    def unregister(self, name: str) -> bool:
        """注销一个技能。返回是否成功。"""
        if name in self._skills:
            del self._skills[name]
            logger.info(f"技能已注销: {name}")
            return True
        return False

    async def execute(self, name: str, **kwargs: Any) -> Any:
        """按名称执行技能。支持同步/异步实现。"""
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"技能 '{name}' 未注册")
        result = skill.impl(**kwargs)
        if hasattr(result, "__await__"):
            result = await result
        return result

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)
