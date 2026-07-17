"""Retrieval Agent 单元测试"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# 确保路径
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("FAST_PATH_ENABLED", "true")
os.environ.setdefault("FAST_PATH_IMAGE_THRESHOLD", "0.85")
os.environ.setdefault("FAST_PATH_TEXT_THRESHOLD", "0.75")
os.environ.setdefault("FAST_PATH_HYBRID_IMAGE_THRESHOLD", "0.90")

from app.agent.retrieval_agent import RetrievalAgent, SkillRegistry


def _make_search_result(
    idx: int,
    score: float = 0.9,
    source: str = "image",
) -> MagicMock:
    """创建模拟 SearchResult"""
    from rag.image_search import SearchResult
    return SearchResult(
        product_id=f"prod_{idx:04d}",
        name=f"Test Product {idx}",
        price=599.0 + idx * 50,
        description=f"Desc {idx}",
        category="运动鞋/跑步鞋",
        image_url=f"http://example.com/{idx}.jpg",
        score=score - idx * 0.05,
        source=source,
        need_clarify=False,
    )


# ============================================================
# 测试 SkillRegistry
# ============================================================


class TestSkillRegistry:
    def test_register_and_get(self):
        registry = SkillRegistry("test")
        registry.register("foo", "desc", {"a": "int"}, lambda: None)
        skill = registry.get("foo")
        assert skill is not None
        assert skill.name == "foo"
        assert skill.description == "desc"

    def test_get_nonexistent(self):
        registry = SkillRegistry("test")
        assert registry.get("missing") is None

    def test_list_skills(self):
        registry = SkillRegistry("test")
        registry.register("a", "desc", {}, lambda: None)
        registry.register("b", "desc", {}, lambda: None)
        assert set(registry.list_skills()) == {"a", "b"}


# ============================================================
# 测试 RetrievalAgent 初始化
# ============================================================


class TestRetrievalAgentInit:
    def test_name(self):
        agent = RetrievalAgent()
        assert agent.name == "retrieval"

    def test_skills_registered(self):
        agent = RetrievalAgent()
        assert "clip_search" in agent.skills.list_skills()
        assert "bge_search" in agent.skills.list_skills()
        assert "hybrid_search" in agent.skills.list_skills()
        assert "rerank" in agent.skills.list_skills()


# ============================================================
# 纯图片检索路径
# ============================================================


class TestImageOnlyRetrieval:

    @pytest.mark.asyncio
    async def test_image_only_high_confidence_fast_path(self):
        """CLIP > 0.85 → 命中快速路径"""
        agent = RetrievalAgent()
        mock_results = [_make_search_result(0, score=0.92)]

        with patch("app.agent.retrieval_agent.search_by_image", return_value=mock_results):
            result = await agent.execute({
                "image_embedding": [0.1] * 512,
                "top_k": 5,
            })

        assert result["agent"] == "retrieval"
        assert result["fast_path_hit"] is True
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["product_id"] == "prod_0000"

    @pytest.mark.asyncio
    async def test_image_only_low_confidence_triggers_rerank(self):
        """CLIP ≤ 0.85 → 触发 rerank"""
        agent = RetrievalAgent()
        mock_results = [_make_search_result(i, score=0.70) for i in range(5)]

        with (
            patch("app.agent.retrieval_agent.search_by_image", return_value=mock_results),
            patch("app.agent.retrieval_agent.rerank", return_value=[
                {"index": 0, "score": 0.95},
                {"index": 1, "score": 0.88},
            ]) as mock_rerank,
        ):
            result = await agent.execute({
                "image_embedding": [0.1] * 512,
                "top_k": 5,
                "query": "红色跑步鞋",
            })

        mock_rerank.assert_called_once()
        assert result["fast_path_hit"] is False
        assert len(result["candidates"]) > 0


# ============================================================
# 纯文本检索路径
# ============================================================


class TestTextOnlyRetrieval:

    @pytest.mark.asyncio
    async def test_text_only_high_confidence_fast_path(self):
        """BGE > 0.75 → 命中快速路径"""
        agent = RetrievalAgent()
        mock_results = [_make_search_result(i, score=0.82, source="text") for i in range(3)]

        with patch("app.agent.retrieval_agent.search_by_text", return_value=mock_results):
            result = await agent.execute({
                "text_embedding": [0.2] * 768,
                "top_k": 5,
                "query": "透气跑步鞋",
            })

        assert result["agent"] == "retrieval"
        assert result["fast_path_hit"] is True
        assert len(result["candidates"]) == 3

    @pytest.mark.asyncio
    async def test_text_only_low_confidence_triggers_rerank(self):
        """BGE ≤ 0.75 → 触发 rerank"""
        agent = RetrievalAgent()
        mock_results = [_make_search_result(i, score=0.65, source="text") for i in range(4)]

        with (
            patch("app.agent.retrieval_agent.search_by_text", return_value=mock_results),
            patch("app.agent.retrieval_agent.rerank", return_value=[
                {"index": i, "score": 0.9 - i * 0.1} for i in range(4)
            ]) as mock_rerank,
        ):
            result = await agent.execute({
                "text_embedding": [0.2] * 768,
                "top_k": 5,
                "query": "性价比高的跑鞋",
            })

        mock_rerank.assert_called_once()
        assert result["fast_path_hit"] is False


# ============================================================
# 图文混合检索路径
# ============================================================


class TestHybridRetrieval:

    @pytest.mark.asyncio
    async def test_hybrid_high_image_confidence_fast_path(self):
        """CLIP ≥ 0.90 → 快速路径（RRF only，无 rerank）"""
        agent = RetrievalAgent()
        image_results = [_make_search_result(i, score=0.95, source="image") for i in range(3)]
        text_results = [_make_search_result(i + 10, score=0.80, source="text") for i in range(3)]

        with (
            patch("app.agent.retrieval_agent.search_by_image", return_value=image_results),
            patch("app.agent.retrieval_agent.search_by_text", return_value=text_results),
            patch("app.agent.retrieval_agent.hybrid_search", return_value=image_results) as mock_hybrid,
            patch("app.agent.retrieval_agent.rerank") as mock_rerank,
        ):
            result = await agent.execute({
                "image_embedding": [0.1] * 512,
                "text_embedding": [0.2] * 768,
                "top_k": 5,
            })

        assert result["fast_path_hit"] is True
        assert len(result["candidates"]) > 0

    @pytest.mark.asyncio
    async def test_hybrid_low_image_confidence_full_pipeline(self):
        """CLIP < 0.90 → 完整路径（hybrid_search + rerank）"""
        agent = RetrievalAgent()
        image_results = [_make_search_result(i, score=0.75, source="image") for i in range(3)]
        text_results = [_make_search_result(i + 10, score=0.80, source="text") for i in range(3)]

        with (
            patch("app.agent.retrieval_agent.search_by_image", return_value=image_results),
            patch("app.agent.retrieval_agent.search_by_text", return_value=text_results),
            patch("app.agent.retrieval_agent.hybrid_search", return_value=image_results + text_results),
        ):
            result = await agent.execute({
                "image_embedding": [0.1] * 512,
                "text_embedding": [0.2] * 768,
                "top_k": 10,
            })

        assert result["fast_path_hit"] is False
        assert len(result["candidates"]) > 0


# ============================================================
# 知识查询路径（纯文本无商品意图）
# ============================================================


class TestKnowledgeQuery:

    @pytest.mark.asyncio
    async def test_knowledge_query_returns_citations(self):
        """纯知识查询 → search_by_text，不走 rerank"""
        agent = RetrievalAgent()
        mock_results = [_make_search_result(i, score=0.70, source="knowledge") for i in range(3)]

        with (
            patch("app.agent.retrieval_agent.search_by_text", return_value=mock_results),
            patch("app.agent.retrieval_agent.rerank") as mock_rerank,
        ):
            result = await agent.execute({
                "text_embedding": [0.3] * 768,
                "top_k": 5,
                "query": "碳板跑鞋和普通跑鞋的区别是什么",
            })

        # 知识查询应走快速路径，不触发 rerank
        mock_rerank.assert_not_called()
        assert result["fast_path_hit"] is True
        assert len(result["candidates"]) == 3


# ============================================================
# 返回结构 & 边界测试
# ============================================================


class TestReturnStructure:

    @pytest.mark.asyncio
    async def test_latency_ms_present(self):
        """返回结果包含 latency_ms"""
        agent = RetrievalAgent()
        with patch("app.agent.retrieval_agent.search_by_image", return_value=[]):
            result = await agent.execute({"image_embedding": [0.1] * 512})

        assert "latency_ms" in result
        assert isinstance(result["latency_ms"], float)
        assert result["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_empty_embeddings_return_empty_candidates(self):
        """空嵌入向量 → 空候选列表"""
        agent = RetrievalAgent()
        result = await agent.execute({})
        assert result["candidates"] == []
        assert result["agent"] == "retrieval"

    @pytest.mark.asyncio
    async def test_fast_path_stats_in_result(self):
        """返回结果包含 fast_path_hit 字段"""
        agent = RetrievalAgent()
        with patch("app.agent.retrieval_agent.search_by_image", return_value=[]):
            result = await agent.execute({"image_embedding": [0.1] * 512})

        assert "fast_path_hit" in result
        assert result["fast_path_hit"] is False
