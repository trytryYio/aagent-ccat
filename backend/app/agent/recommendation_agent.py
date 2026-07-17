"""Recommendation Agent：生成推荐理由 + 处理澄清对话"""
from __future__ import annotations

import logging
from typing import Any

from app.agent.skill_registry import SkillRegistry, Skill

logger = logging.getLogger(__name__)

# 澄清触发阈值
CLARIFY_SCORE_THRESHOLD = 0.6
CLARIFY_GAP_THRESHOLD = 0.05


class RecommendationAgent:
    """推荐 Agent：生成推荐理由 + 处理澄清对话"""

    name = "recommendation"

    def __init__(self) -> None:
        self.skills = SkillRegistry()
        self._register_skills()

    # ------------------------------------------------------------------
    # Skill 注册
    # ------------------------------------------------------------------

    def _register_skills(self) -> None:
        self.skills.register(Skill(
            name="load_user_profile",
            description="从 Redis 加载用户画像",
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户 ID"},
                },
                "required": ["user_id"],
            },
            impl=self._load_user_profile,
        ))
        self.skills.register(Skill(
            name="generate_recommendation",
            description="基于候选商品和用户画像生成推荐文案",
            parameters={
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "候选商品列表",
                    },
                    "profile": {
                        "type": "object",
                        "description": "用户画像",
                    },
                    "citations": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "引用知识片段",
                    },
                },
                "required": ["candidates"],
            },
            impl=self._generate_recommendation,
        ))
        self.skills.register(Skill(
            name="decide_clarify",
            description="基于分数和槽位判断是否需要发起澄清",
            parameters={
                "type": "object",
                "properties": {
                    "scores": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "候选分数列表",
                    },
                    "slots": {
                        "type": "object",
                        "description": "已填充槽位",
                    },
                },
                "required": ["scores"],
            },
            impl=self._decide_clarify,
        ))
        self.skills.register(Skill(
            name="generate_clarify_question",
            description="基于缺失槽位生成澄清问题",
            parameters={
                "type": "object",
                "properties": {
                    "missing_slots": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "缺失槽位列表",
                    },
                },
                "required": ["missing_slots"],
            },
            impl=self._generate_clarify_question,
        ))

    # ------------------------------------------------------------------
    # Skill 实现
    # ------------------------------------------------------------------

    async def _load_user_profile(self, user_id: str) -> dict[str, Any]:
        """从 Redis 加载用户画像（通过 ProfileStore）"""
        try:
            from app.memory.profile_store import profile_store
            profile = await profile_store.get(user_id)
            if profile is None:
                return {}
            return profile.to_dict()
        except Exception as e:
            logger.warning(f"加载用户画像失败 user_id={user_id}: {e}")
            return {}

    async def _generate_recommendation(
        self,
        candidates: list[dict],
        profile: dict | None = None,
        citations: list[dict] | None = None,
    ) -> dict[str, Any]:
        """生成推荐文案"""
        if not candidates:
            return {
                "recommendation": "",
                "need_clarify": False,
            }

        top = candidates[0]
        name = top.get("name", "未知商品")
        price = top.get("price")
        category = top.get("category", "")

        parts = [f"为您推荐：{name}"]
        if price is not None:
            parts.append(f"价格 {price} 元")
        if category:
            parts.append(f"品类：{category}")

        # 结合用户画像
        if profile:
            brands = profile.get("preferred_brands", [])
            if brands:
                parts.append(f"符合您偏好的品牌：{', '.join(brands)}")
            budget = profile.get("budget_range")
            if budget and price is not None:
                if isinstance(budget, (list, tuple)) and len(budget) == 2:
                    if budget[0] <= price <= budget[1]:
                        parts.append("价格在您的预算范围内")
                    else:
                        parts.append("价格略超出您的预算范围")

        # 结合引用知识
        if citations:
            snippets = [c.get("snippet", "") for c in citations[:2] if c.get("snippet")]
            if snippets:
                parts.append("亮点：" + "；".join(snippets))

        return {
            "recommendation": "，".join(parts),
            "need_clarify": False,
        }

    async def _decide_clarify(
        self,
        scores: list[float],
        slots: dict | None = None,
    ) -> dict[str, Any]:
        """基于分数判断是否需要澄清（rule-based）"""
        if not scores:
            return {"need_clarify": True, "reason": "无候选分数"}

        sorted_scores = sorted(scores, reverse=True)
        top1 = sorted_scores[0]
        top2 = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
        gap = top1 - top2

        reasons: list[str] = []
        if top1 < CLARIFY_SCORE_THRESHOLD:
            reasons.append(f"top1 score {top1:.2f} < {CLARIFY_SCORE_THRESHOLD}")
        if len(sorted_scores) > 1 and gap < CLARIFY_GAP_THRESHOLD:
            reasons.append(f"top1-top2 gap {gap:.3f} < {CLARIFY_GAP_THRESHOLD}")

        # 槽位缺失也会触发澄清
        missing_slots: list[str] = []
        if slots is not None:
            if not slots.get("category"):
                missing_slots.append("category")
            if not slots.get("budget"):
                missing_slots.append("budget")

        need_clarify = bool(reasons) or bool(missing_slots)
        return {
            "need_clarify": need_clarify,
            "reasons": reasons,
            "missing_slots": missing_slots,
        }

    async def _generate_clarify_question(
        self,
        missing_slots: list[str],
    ) -> dict[str, Any]:
        """基于缺失槽位生成澄清问题（template-based）"""
        if not missing_slots:
            return {"question": "能详细描述一下您的需求吗？"}

        slot_questions = {
            "category": "您想购买哪类商品？",
            "budget": "您的预算范围是多少？",
            "brand": "您有偏好的品牌吗？",
            "style": "您偏好什么风格？",
            "color": "您有颜色偏好吗？",
            "size": "您需要什么尺码？",
        }

        questions = [slot_questions.get(s, f"能告诉我您的 {s} 偏好吗？") for s in missing_slots]
        return {"question": "".join(questions)}

    # ------------------------------------------------------------------
    # 主执行入口
    # ------------------------------------------------------------------

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """执行推荐任务

        Args:
            task: {
                "candidates": [...],
                "scores": [...],
                "slots": {...},
                "user_id": "xxx",
                "citations": [...],
            }

        Returns:
            {
                "recommendation": "...",
                "need_clarify": False,
                "clarify_question": "...",
                "agent": "recommendation",
            }
        """
        candidates = task.get("candidates", [])
        scores = task.get("scores", [])
        slots = task.get("slots", {})
        user_id = task.get("user_id", "")
        citations = task.get("citations", [])

        # 1. candidates 为空 → 友好提示
        if not candidates:
            return {
                "recommendation": "抱歉，暂时没有找到合适的商品，请尝试其他关键词或上传图片。",
                "need_clarify": False,
                "agent": "recommendation",
            }

        # 2. 判断是否需要澄清
        clarify_result = await self.skills.execute("decide_clarify", scores=scores, slots=slots)

        if clarify_result["need_clarify"]:
            missing = clarify_result.get("missing_slots", [])
            question_result = await self.skills.execute(
                "generate_clarify_question", missing_slots=missing
            )
            return {
                "recommendation": "",
                "need_clarify": True,
                "clarify_question": question_result["question"],
                "reasons": clarify_result.get("reasons", []),
                "agent": "recommendation",
            }

        # 3. 生成推荐理由
        profile: dict = {}
        if user_id:
            profile = await self.skills.execute("load_user_profile", user_id=user_id)

        rec_result = await self.skills.execute(
            "generate_recommendation",
            candidates=candidates,
            profile=profile,
            citations=citations,
        )
        return {
            "recommendation": rec_result["recommendation"],
            "need_clarify": False,
            "agent": "recommendation",
        }
