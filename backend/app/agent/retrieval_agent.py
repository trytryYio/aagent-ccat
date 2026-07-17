"""Retrieval Agent：专职多模态检索"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from rag.image_search import SearchResult
from rag.image_search import search_by_image
from rag.text_retrieval import search_by_text
from rag.hybrid_search import hybrid_search
from rag.rerank import rerank

logger = logging.getLogger(__name__)


# ====== 阈值配置（与 hybrid_search 保持一致）======
from rag.hybrid_search import (  # noqa: E402
    FAST_PATH_ENABLED,
    FAST_PATH_IMAGE_THRESHOLD,
    FAST_PATH_TEXT_THRESHOLD,
    FAST_PATH_HYBRID_IMAGE_THRESHOLD,
)


@dataclass
class Skill:
    """注册的检索技能"""
    name: str
    description: str
    parameters: dict[str, str]
    fn: Callable[..., Any]


class SkillRegistry:
    """技能注册表"""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self._skills: dict[str, Skill] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, str],
        fn: Callable[..., Any],
    ):
        self._skills[name] = Skill(
            name=name,
            description=description,
            parameters=parameters,
            fn=fn,
        )
        logger.info(f"[{self.agent_name}] registered skill: {name}")

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())


class RetrievalAgent:
    """检索 Agent：专职多模态检索

    内部决策逻辑（TOA 自主工具选择）:
    1. 纯图片 → clip_search
       - CLIP > 0.85 → 直接返回
       - CLIP ≤ 0.85 → rerank 精排

    2. 纯文本 → bge_search
       - BGE > 0.75 → 直接返回
       - BGE ≤ 0.75 → rerank 精排

    3. 图文混合 → hybrid_search
       - CLIP > 0.90 → 只做 RRF
       - CLIP ≤ 0.90 → RRF + rerank

    4. 纯文本知识查询（无图无商品意图）→ search_citations
    """

    name = "retrieval"

    def __init__(self):
        self.skills = SkillRegistry("retrieval")
        self._register_skills()

    def _register_skills(self):
        """从 rag/ 模块注册已有检索能力"""
        self.skills.register(
            "clip_search",
            "CLIP 视觉向量检索",
            {"image_embedding": "list", "top_k": "int"},
            self._clip_search,
        )
        self.skills.register(
            "bge_search",
            "BGE-M3 语义向量检索",
            {"text_embedding": "list", "top_k": "int"},
            self._bge_search,
        )
        self.skills.register(
            "hybrid_search",
            "混合检索 + RRF 融合",
            {"image_embedding": "list", "text_embedding": "list", "top_k": "int"},
            self._hybrid_search_impl,
        )
        self.skills.register(
            "rerank",
            "精排候选商品",
            {"query": "str", "documents": "list"},
            rerank,
        )

    def _clip_search(self, image_embedding: list, top_k: int = 10) -> list[SearchResult]:
        """CLIP 图像检索包装"""
        return search_by_image(image_embedding, top_k=top_k * 2)

    def _bge_search(self, text_embedding: list, top_k: int = 10) -> list[SearchResult]:
        """BGE 文本检索包装"""
        return search_by_text(text_embedding, top_k=top_k * 2)

    def _hybrid_search_impl(
        self,
        image_embedding: list,
        text_embedding: list,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """混合检索包装"""
        return hybrid_search(
            image_embedding=image_embedding,
            text_embedding=text_embedding,
            top_k=top_k,
        )

    async def execute(self, task: dict) -> dict:
        """执行检索任务

        Args:
            task: {
                "image_embedding": [...],
                "text_embedding": [...],
                "slots": {"budget": [500,1000], "category": "跑步鞋"},
                "top_k": 10,
                "query": "可选查询文本",
            }

        Returns:
            {
                "candidates": [...],
                "latency_ms": 320.0,
                "fast_path_hit": True,
                "agent": "retrieval",
            }
        """
        start_time = time.monotonic()
        fast_path_hit = False

        image_embedding = task.get("image_embedding") or []
        text_embedding = task.get("text_embedding") or []
        top_k = task.get("top_k", 10)
        query = task.get("query", "")
        slots = task.get("slots", {})

        has_image = bool(image_embedding)
        has_text = bool(text_embedding)

        candidates: list[dict] = []

        if has_image and not has_text:
            # 路径1: 纯图片 → clip_search
            fast_path_hit, candidates = await self._execute_image_only(
                image_embedding, top_k, query
            )

        elif has_text and not has_image:
            # 路径2 / 路径4: 判断是知识查询还是商品检索
            if self._is_knowledge_query(query, slots):
                fast_path_hit, candidates = await self._execute_knowledge_query(
                    text_embedding, top_k, query
                )
            else:
                fast_path_hit, candidates = await self._execute_text_only(
                    text_embedding, top_k, query
                )

        elif has_image and has_text:
            # 路径3: 图文混合 → hybrid_search
            fast_path_hit, candidates = await self._execute_hybrid(
                image_embedding, text_embedding, top_k, query
            )

        latency_ms = (time.monotonic() - start_time) * 1000.0

        return {
            "candidates": candidates,
            "latency_ms": round(latency_ms, 2),
            "fast_path_hit": fast_path_hit,
            "agent": self.name,
        }

    def _is_knowledge_query(self, query: str, slots: dict) -> bool:
        """判断是否为纯知识查询（无商品购买意图）"""
        if not query:
            return False
        # 纯知识类问题模式
        knowledge_patterns = ["怎么", "如何", "什么是", "区别", "对比", "哪个好"]
        has_product_slots = bool(slots.get("category") or slots.get("budget"))
        has_knowledge_intent = any(p in query for p in knowledge_patterns)
        return has_knowledge_intent and not has_product_slots

    async def _execute_image_only(
        self,
        image_embedding: list,
        top_k: int,
        query: str,
    ) -> tuple[bool, list[dict]]:
        """纯图片检索路径"""
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, search_by_image, image_embedding, top_k * 2
        )
        if not results:
            return False, []

        max_score = results[0].score
        fast_path_hit = FAST_PATH_ENABLED and max_score >= FAST_PATH_IMAGE_THRESHOLD

        if not fast_path_hit:
            # CLIP ≤ 0.85 → rerank 精排
            docs = self._results_to_docs(results)
            reranked = await loop.run_in_executor(None, rerank, query or "image search", docs[:20])
            rerank_map = {item["index"]: item["score"] for item in reranked}
            for i, r in enumerate(results):
                r.score = rerank_map.get(i, r.score)
            results.sort(key=lambda x: x.score, reverse=True)

        candidates = [self._result_to_dict(r) for r in results[:top_k]]
        return fast_path_hit, candidates

    async def _execute_text_only(
        self,
        text_embedding: list,
        top_k: int,
        query: str,
    ) -> tuple[bool, list[dict]]:
        """纯文本检索路径"""
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, search_by_text, text_embedding, top_k * 2
        )
        if not results:
            return False, []

        max_score = results[0].score
        fast_path_hit = FAST_PATH_ENABLED and max_score >= FAST_PATH_TEXT_THRESHOLD

        if not fast_path_hit:
            docs = self._results_to_docs(results)
            reranked = await loop.run_in_executor(None, rerank, query or "text search", docs[:20])
            rerank_map = {item["index"]: item["score"] for item in reranked}
            for i, r in enumerate(results):
                r.score = rerank_map.get(i, r.score)
            results.sort(key=lambda x: x.score, reverse=True)

        candidates = [self._result_to_dict(r) for r in results[:top_k]]
        return fast_path_hit, candidates

    async def _execute_hybrid(
        self,
        image_embedding: list,
        text_embedding: list,
        top_k: int,
        query: str,
    ) -> tuple[bool, list[dict]]:
        """图文混合检索路径"""
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: hybrid_search(
                image_embedding=image_embedding,
                text_embedding=text_embedding,
                top_k=top_k,
                query=query,
            ),
        )
        if not results:
            return False, []

        # 快速路径判断：CLIP score >= 0.90
        image_results = await loop.run_in_executor(
            None, search_by_image, image_embedding, top_k * 2
        )
        fast_path_hit = bool(
            image_results
            and FAST_PATH_ENABLED
            and image_results[0].score >= FAST_PATH_HYBRID_IMAGE_THRESHOLD
        )

        candidates = [self._result_to_dict(r) for r in results[:top_k]]
        return fast_path_hit, candidates

    async def _execute_knowledge_query(
        self,
        text_embedding: list,
        top_k: int,
        query: str,
    ) -> tuple[bool, list[dict]]:
        """知识查询路径（无商品意图）"""
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, search_by_text, text_embedding, top_k
        )
        candidates = [self._result_to_dict(r) for r in results[:top_k]]
        # 知识查询默认走快速路径（无需 rerank）
        return True, candidates

    @staticmethod
    def _results_to_docs(results: list[SearchResult]) -> list[dict]:
        return [
            {
                "product_id": r.product_id,
                "name": r.name,
                "description": r.description,
            }
            for r in results
        ]

    @staticmethod
    def _result_to_dict(result: SearchResult) -> dict:
        return {
            "product_id": result.product_id,
            "name": result.name,
            "price": result.price,
            "category": result.category,
            "image_url": result.image_url,
            "score": result.score,
            "source": result.source,
        }
