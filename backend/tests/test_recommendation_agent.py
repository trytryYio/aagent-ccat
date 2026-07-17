"""Recommendation Agent 单元测试"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# 确保路径
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.agent.recommendation_agent import RecommendationAgent


# ============================================================
#  fixtures
# ============================================================


@pytest.fixture
def agent():
    return RecommendationAgent()


# ============================================================
#  初始化 & Skill 注册
# ============================================================


class TestRecommendationAgentInit:
    def test_name(self, agent):
        assert agent.name == "recommendation"

    def test_skills_registered(self, agent):
        skills = agent.skills.list_skills()
        skill_names = {s.name for s in skills}
        assert "load_user_profile" in skill_names
        assert "generate_recommendation" in skill_names
        assert "decide_clarify" in skill_names
        assert "generate_clarify_question" in skill_names


# ============================================================
#  空候选列表 -> 友好提示
# ============================================================


class TestEmptyCandidates:

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_friendly_message(self, agent):
        result = await agent.execute({"candidates": []})
        assert result["agent"] == "recommendation"
        assert result["need_clarify"] is False
        assert "暂时没有找到合适的商品" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_missing_candidates_key_returns_friendly_message(self, agent):
        result = await agent.execute({})
        assert result["need_clarify"] is False
        assert "暂时没有找到合适的商品" in result["recommendation"]


# ============================================================
#  低分/低 gap -> 触发澄清
# ============================================================


class TestClarifyTrigger:

    @pytest.mark.asyncio
    async def test_low_top1_score_triggers_clarify(self, agent):
        """top1 score < 0.6 -> 触发澄清"""
        result = await agent.execute({
            "candidates": [{"name": "Test", "price": 500}],
            "scores": [0.45],
            "slots": {},
        })
        assert result["need_clarify"] is True
        assert "clarify_question" in result

    @pytest.mark.asyncio
    async def test_small_gap_triggers_clarify(self, agent):
        """top1-top2 gap < 0.05 -> 触发澄清"""
        result = await agent.execute({
            "candidates": [{"name": "A", "price": 500}, {"name": "B", "price": 520}],
            "scores": [0.75, 0.72],
            "slots": {"category": "跑步鞋", "budget": [400, 800]},
        })
        assert result["need_clarify"] is True

    @pytest.mark.asyncio
    async def test_missing_slots_triggers_clarify(self, agent):
        """缺少 category/budget -> 触发澄清"""
        result = await agent.execute({
            "candidates": [{"name": "Test", "price": 500}],
            "scores": [0.9],
            "slots": {},
        })
        assert result["need_clarify"] is True
        assert "clarify_question" in result


# ============================================================
#  正常路径 -> 生成推荐理由
# ============================================================


class TestGenerateRecommendation:

    @pytest.mark.asyncio
    async def test_high_score_generates_recommendation(self, agent):
        """高分 + 完整槽位 -> 生成推荐"""
        result = await agent.execute({
            "candidates": [{"name": "飞电6", "price": 899, "category": "跑步鞋"}],
            "scores": [0.95],
            "slots": {"category": "跑步鞋", "budget": [500, 1000]},
        })
        assert result["need_clarify"] is False
        assert "飞电6" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_recommendation_includes_price_and_category(self, agent):
        result = await agent.execute({
            "candidates": [{"name": "Test Shoe", "price": 599, "category": "篮球鞋"}],
            "scores": [0.9],
            "slots": {"category": "篮球鞋", "budget": [400, 800]},
        })
        assert "599" in result["recommendation"]
        assert "篮球鞋" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_recommendation_with_citations(self, agent):
        result = await agent.execute({
            "candidates": [{"name": "Test", "price": 500}],
            "scores": [0.9],
            "slots": {"category": "跑步鞋", "budget": [400, 800]},
            "citations": [{"snippet": "缓震科技优秀"}, {"snippet": "轻量设计"}],
        })
        assert "缓震科技优秀" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_recommendation_with_user_profile(self, agent):
        """加载用户画像后结合偏好"""
        with patch.object(
            agent.skills,
            "execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            async def side_effect(name, **kwargs):
                if name == "decide_clarify":
                    return {"need_clarify": False, "reasons": [], "missing_slots": []}
                if name == "load_user_profile":
                    return {
                        "preferred_brands": ["李宁"],
                        "budget_range": [400, 800],
                    }
                if name == "generate_recommendation":
                    return {
                        "recommendation": "为您推荐：Test，符合您偏好的品牌：李宁",
                        "need_clarify": False,
                    }
                return {}

            mock_exec.side_effect = side_effect
            result = await agent.execute({
                "candidates": [{"name": "Test", "price": 600}],
                "scores": [0.9],
                "slots": {"category": "跑步鞋", "budget": [400, 800]},
                "user_id": "u123",
            })
        assert result["agent"] == "recommendation"


# ============================================================
#  decide_clarify rule 验证
# ============================================================


class TestDecideClarify:

    @pytest.mark.asyncio
    async def test_no_scores_returns_clarify(self, agent):
        result = await agent.skills.execute("decide_clarify", scores=[])
        assert result["need_clarify"] is True

    @pytest.mark.asyncio
    async def test_high_score_no_missing_no_clarify(self, agent):
        result = await agent.skills.execute(
            "decide_clarify",
            scores=[0.95, 0.7],
            slots={"category": "跑步鞋", "budget": [400, 800]},
        )
        assert result["need_clarify"] is False

    @pytest.mark.asyncio
    async def test_gap_exactly_at_threshold(self, agent):
        """gap == 0.05 时不触发（需 < 0.05）"""
        result = await agent.skills.execute(
            "decide_clarify",
            scores=[0.8, 0.75],
            slots={"category": "跑步鞋", "budget": [400, 800]},
        )
        assert result["need_clarify"] is False


# ============================================================
#  generate_clarify_question
# ============================================================


class TestGenerateClarifyQuestion:

    @pytest.mark.asyncio
    async def test_empty_missing_slots(self, agent):
        result = await agent.skills.execute("generate_clarify_question", missing_slots=[])
        assert "question" in result

    @pytest.mark.asyncio
    async def test_category_slot_question(self, agent):
        result = await agent.skills.execute(
            "generate_clarify_question", missing_slots=["category"]
        )
        assert "哪类" in result["question"]

    @pytest.mark.asyncio
    async def test_budget_slot_question(self, agent):
        result = await agent.skills.execute(
            "generate_clarify_question", missing_slots=["budget"]
        )
        assert "预算" in result["question"]

    @pytest.mark.asyncio
    async def test_multiple_slots(self, agent):
        result = await agent.skills.execute(
            "generate_clarify_question", missing_slots=["category", "budget"]
        )
        assert "哪类" in result["question"]
        assert "预算" in result["question"]
