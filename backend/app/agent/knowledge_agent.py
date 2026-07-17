"""Knowledge Agent：回答产品科技参数/品牌知识问题"""
from __future__ import annotations

import logging
from typing import Any

from app.agent.skill_registry import SkillRegistry, Skill

logger = logging.getLogger(__name__)

# 知识不足时的降级回复
FALLBACK_ANSWER = "暂无相关信息，建议咨询客服。"

# 知识类问题路由关键词
KNOWLEDGE_PATTERNS = ["怎么", "如何", "什么是", "区别", "对比", "哪个好", "有碳板", "科技", "参数"]


class KnowledgeAgent:
    """知识 Agent：回答产品科技参数/品牌知识问题"""

    name = "knowledge"

    def __init__(self) -> None:
        self.skills = SkillRegistry()
        self._register_skills()

    # ------------------------------------------------------------------
    # Skill 注册
    # ------------------------------------------------------------------

    def _register_skills(self) -> None:
        self.skills.register(Skill(
            name="search_knowledge_base",
            description="在 Qdrant citations 检索知识片段",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询文本"},
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            impl=self._search_knowledge_base,
        ))
        self.skills.register(Skill(
            name="get_product_specs",
            description="查询商品详细参数",
            parameters={
                "type": "object",
                "properties": {
                    "sku_id": {"type": "string", "description": "商品 SKU ID"},
                },
                "required": ["sku_id"],
            },
            impl=self._get_product_specs,
        ))
        self.skills.register(Skill(
            name="compare_products",
            description="对比两款商品",
            parameters={
                "type": "object",
                "properties": {
                    "sku_a": {"type": "string", "description": "商品 A 的 SKU ID"},
                    "sku_b": {"type": "string", "description": "商品 B 的 SKU ID"},
                },
                "required": ["sku_a", "sku_b"],
            },
            impl=self._compare_products,
        ))
        self.skills.register(Skill(
            name="generate_knowledge_answer",
            description="基于知识生成回答",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户查询"},
                    "context": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "知识上下文片段",
                    },
                },
                "required": ["query", "context"],
            },
            impl=self._generate_knowledge_answer,
        ))

    # ------------------------------------------------------------------
    # Skill 实现
    # ------------------------------------------------------------------

    async def _search_knowledge_base(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """在 Qdrant citations 检索知识片段"""
        try:
            from rag.text_retrieval import get_citations
            from rag.embedding import embed_text

            loop = __import__("asyncio").get_running_loop()
            embedding = await loop.run_in_executor(None, embed_text, query)
            if not embedding:
                return []
            results = await loop.run_in_executor(
                None,
                lambda: get_citations(embedding, [], top_k=top_k, query=query),
            )
            return results if results else []
        except Exception as e:
            logger.warning(f"知识库检索失败 query={query!r}: {e}")
            return []

    async def _get_product_specs(self, sku_id: str) -> dict[str, Any]:
        """查询商品详细参数"""
        try:
            from rag.text_retrieval import get_citations_by_sku

            loop = __import__("asyncio").get_running_loop()
            citations = await loop.run_in_executor(None, get_citations_by_sku, sku_id)
            if not citations:
                return {"sku_id": sku_id, "specs": {}, "found": False}
            return {
                "sku_id": sku_id,
                "specs": {c.get("tag", "info"): c.get("snippet", "") for c in citations},
                "found": True,
            }
        except Exception as e:
            logger.warning(f"查询商品参数失败 sku_id={sku_id}: {e}")
            return {"sku_id": sku_id, "specs": {}, "found": False}

    async def _compare_products(
        self,
        sku_a: str,
        sku_b: str,
    ) -> dict[str, Any]:
        """对比两款商品"""
        spec_a, spec_b = await __import__("asyncio").gather(
            self.skills.execute("get_product_specs", sku_id=sku_a),
            self.skills.execute("get_product_specs", sku_id=sku_b),
        )
        return {
            "sku_a": spec_a,
            "sku_b": spec_b,
            "comparable": bool(spec_a.get("found") and spec_b.get("found")),
        }

    async def _generate_knowledge_answer(
        self,
        query: str,
        context: list[dict],
    ) -> dict[str, Any]:
        """基于知识生成回答"""
        if not context:
            return {"answer": FALLBACK_ANSWER, "confidence": 0.0}

        snippets = [c.get("snippet", c.get("content", "")) for c in context if c]
        snippets = [s for s in snippets if s.strip()]

        if not snippets:
            return {"answer": FALLBACK_ANSWER, "confidence": 0.0}

        answer = "\n".join(f"- {s}" for s in snippets[:3])
        return {"answer": answer, "confidence": 0.8}

    # ------------------------------------------------------------------
    # 路由判断
    # ------------------------------------------------------------------

    @staticmethod
    def should_route(query: str) -> bool:
        """判断查询是否应路由到 knowledge agent"""
        if not query:
            return False
        return any(p in query for p in KNOWLEDGE_PATTERNS)

    # ------------------------------------------------------------------
    # 主执行入口
    # ------------------------------------------------------------------

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """执行知识问答任务

        Args:
            task: {
                "query": "䨻科技是什么",
                "sku_id": "可选",
                "sku_a": "可选",
                "sku_b": "可选",
                "top_k": 5,
            }

        Returns:
            {
                "answer": "...",
                "confidence": 0.8,
                "agent": "knowledge",
            }
        """
        query = task.get("query", "")
        top_k = task.get("top_k", 5)

        if not query:
            return {
                "answer": FALLBACK_ANSWER,
                "confidence": 0.0,
                "agent": "knowledge",
            }

        # 1. 先检索知识库
        context = await self.skills.execute(
            "search_knowledge_base", query=query, top_k=top_k
        )

        # 2. 如果有具体 SKU，补充商品参数
        sku_id = task.get("sku_id")
        if sku_id:
            specs = await self.skills.execute("get_product_specs", sku_id=sku_id)
            if specs.get("found"):
                for k, v in specs.get("specs", {}).items():
                    context.append({"snippet": f"{k}: {v}", "source": "specs"})

        # 3. 如果知识不足 → 降级回复
        if not context:
            return {
                "answer": FALLBACK_ANSWER,
                "confidence": 0.0,
                "agent": "knowledge",
            }

        # 4. 生成回答
        result = await self.skills.execute(
            "generate_knowledge_answer", query=query, context=context
        )
        return {
            "answer": result["answer"],
            "confidence": result.get("confidence", 0.0),
            "agent": "knowledge",
        }
