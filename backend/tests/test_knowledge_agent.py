"""Knowledge Agent 单元测试"""
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

from app.agent.knowledge_agent import KnowledgeAgent, KNOWLEDGE_PATTERNS, FALLBACK_ANSWER


# ============================================================
#  fixtures
# ============================================================


@pytest.fixture
def agent():
    return KnowledgeAgent()


# ============================================================
#  初始化 & Skill 注册
# ============================================================


class TestKnowledgeAgentInit:
    def test_name(self, agent):
        assert agent.name == "knowledge"

    def test_skills_registered(self, agent):
        skills = agent.skills.list_skills()
        skill_names = {s.name for s in skills}
        assert "search_knowledge_base" in skill_names
        assert "get_product_specs" in skill_names
        assert "compare_products" in skill_names
        assert "generate_knowledge_answer" in skill_names


# ============================================================
#  路由判断
# ============================================================


class TestShouldRoute:
    def test_knowledge_query_with_什么是(self):
        assert KnowledgeAgent.should_route("什么是䨻科技") is True

    def test_knowledge_query_with_区别(self):
        assert KnowledgeAgent.should_route("碳板跑鞋和普通跑鞋的区别") is True

    def test_knowledge_query_with_对比(self):
        assert KnowledgeAgent.should_route("这两款怎么选") is True

    def test_knowledge_query_with_科技(self):
        assert KnowledgeAgent.should_route("飞电6有碳板吗") is True

    def test_non_knowledge_query(self):
        assert KnowledgeAgent.should_route("我想买一双跑鞋") is False

    def test_empty_query(self):
        assert KnowledgeAgent.should_route("") is False


# ============================================================
#  execute: 知识检索 + 回答
# ============================================================


class TestExecuteKnowledgeSearch:

    @pytest.mark.asyncio
    async def test_search_knowledge_base_returns_results(self, agent):
        mock_results = [
            {"snippet": "䨻科技是李宁的一种缓震材料", "source": "knowledge"},
        ]
        with patch.object(
            agent.skills, "execute", new_callable=AsyncMock
        ) as mock_exec:
            async def side_effect(name, **kwargs):
                if name == "search_knowledge_base":
                    return mock_results
                if name == "generate_knowledge_answer":
                    return {"answer": "- 䨻科技是李宁的一种缓震材料", "confidence": 0.8}
                return {}

            mock_exec.side_effect = side_effect
            result = await agent.execute({"query": "什么是䨻科技"})

        assert result["agent"] == "knowledge"
        assert result["confidence"] > 0
        assert "䨻科技" in result["answer"]

    @pytest.mark.asyncio
    async def test_no_context_returns_fallback(self, agent):
        """知识不足时返回降级回复"""
        with patch.object(
            agent.skills, "execute", new_callable=AsyncMock
        ) as mock_exec:
            async def side_effect(name, **kwargs):
                if name == "search_knowledge_base":
                    return []
                return {}

            mock_exec.side_effect = side_effect
            result = await agent.execute({"query": "什么是䨻科技"})

        assert result["answer"] == FALLBACK_ANSWER
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_empty_query_returns_fallback(self, agent):
        result = await agent.execute({"query": ""})
        assert result["answer"] == FALLBACK_ANSWER
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_answer_includes_snippets(self, agent):
        mock_results = [
            {"snippet": "碳板提供推进力"},
            {"snippet": "适合竞速场景"},
        ]
        with patch.object(
            agent.skills, "execute", new_callable=AsyncMock
        ) as mock_exec:
            async def side_effect(name, **kwargs):
                if name == "search_knowledge_base":
                    return mock_results
                if name == "generate_knowledge_answer":
                    return {
                        "answer": "- 碳板提供推进力\n- 适合竞速场景",
                        "confidence": 0.8,
                    }
                return {}

            mock_exec.side_effect = side_effect
            result = await agent.execute({"query": "碳板有什么用"})

        assert "碳板" in result["answer"]


# ============================================================
#  get_product_specs (mocked external)
# ============================================================


class TestGetProductSpecs:

    @pytest.mark.asyncio
    async def test_get_product_specs_not_found(self, agent):
        with patch("rag.text_retrieval.get_citations_by_sku", return_value=[], create=True):
            result = await agent.skills.execute("get_product_specs", sku_id="SKU999")
        assert result["found"] is False
        assert result["sku_id"] == "SKU999"

    @pytest.mark.asyncio
    async def test_get_product_specs_found(self, agent):
        mock_citations = [
            {"tag": "中底", "snippet": "䨻科技缓震"},
            {"tag": "外底", "snippet": "橡胶耐磨"},
        ]
        with patch("rag.text_retrieval.get_citations_by_sku", return_value=mock_citations, create=True):
            result = await agent.skills.execute("get_product_specs", sku_id="SKU001")
        assert result["found"] is True
        assert "中底" in result["specs"]


# ============================================================
#  compare_products (mocked external)
# ============================================================


class TestCompareProducts:

    @pytest.mark.asyncio
    async def test_compare_one_not_found(self, agent):
        mock_citations_a = [{"tag": "info", "snippet": "detail A"}]
        with patch(
            "rag.text_retrieval.get_citations_by_sku",
            side_effect=[mock_citations_a, []],
            create=True,
        ):
            result = await agent.skills.execute(
                "compare_products", sku_a="SKU_A", sku_b="SKU_B"
            )
        assert result["comparable"] is False


# ============================================================
#  generate_knowledge_answer
# ============================================================


class TestGenerateKnowledgeAnswer:

    @pytest.mark.asyncio
    async def test_empty_context_returns_fallback(self, agent):
        result = await agent.skills.execute(
            "generate_knowledge_answer", query="test", context=[]
        )
        assert result["answer"] == FALLBACK_ANSWER
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_context_generates_answer(self, agent):
        context = [{"snippet": "知识片段1"}, {"snippet": "知识片段2"}]
        result = await agent.skills.execute(
            "generate_knowledge_answer", query="test", context=context
        )
        assert "知识片段1" in result["answer"]
        assert result["confidence"] > 0
