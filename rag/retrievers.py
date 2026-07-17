"""检索器抽象层：统一接口 + 4 种检索器实现

每个 Retriever 封装一种检索策略，通过 QueryRouter 按输入类型自动路由。
新增检索方式只需继承 BaseRetriever 并注册到 Router，不改节点代码。
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class BaseRetriever(ABC):
    """检索器基类"""

    @abstractmethod
    async def retrieve(
        self,
        query: str = "",
        image_embedding: Optional[list[float]] = None,
        text_embedding: Optional[list[float]] = None,
        top_k: int = 10,
        filters: Optional[dict] = None,
        tenant_id: str = "default",
    ) -> list[dict]:
        """统一检索接口"""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


class ImageRetriever(BaseRetriever):
    """图像检索：CLIP 512d → Qdrant image 向量"""

    async def retrieve(self, image_embedding=None, top_k=5, tenant_id="default", **kw) -> list[dict]:
        from app.graph.tools import search_by_image_tool
        if not image_embedding:
            return []
        return await search_by_image_tool(image_embedding, top_k=top_k, tenant_id=tenant_id)


class TextRetriever(BaseRetriever):
    """文本检索：BGE-M3 1024d → Qdrant text 向量 + payload 过滤"""

    async def retrieve(
        self, query="", text_embedding=None, top_k=10,
        filters=None, tenant_id="default", **kw
    ) -> list[dict]:
        from app.graph.tools import search_by_text_tool
        if not text_embedding:
            return []
        qdrant_filter = (filters or {}).get("qdrant_filter")
        return await search_by_text_tool(
            text_embedding, top_k=top_k,
            tenant_id=tenant_id,
            qdrant_filter=qdrant_filter,
        )


class HybridRetriever(BaseRetriever):
    """混合检索：图像+文本双路 → RRF 融合"""

    async def retrieve(
        self, query="", image_embedding=None, text_embedding=None,
        top_k=10, filters=None, tenant_id="default", **kw
    ) -> list[dict]:
        from app.graph.tools import hybrid_search_tool
        if not image_embedding and not text_embedding:
            return []
        qdrant_filter = (filters or {}).get("qdrant_filter")
        return await hybrid_search_tool(
            image_embedding=image_embedding,
            text_embedding=text_embedding,
            query=query,
            top_k=top_k,
            tenant_id=tenant_id,
            qdrant_filter=qdrant_filter,
        )


class DocumentRetriever(BaseRetriever):
    """文档检索：BGE-M3 1024d → Qdrant documents 集合，直接调用 client"""

    async def retrieve(
        self, query="", text_embedding=None, top_k=5,
        filters=None, tenant_id="default", **kw
    ) -> list[dict]:
        import asyncio
        from rag.db_client import get_qdrant_client
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        if not text_embedding:
            return []
        client = get_qdrant_client()
        filter_conditions = []
        if tenant_id and tenant_id != "default":
            filter_conditions.append(
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
            )
        query_filter = Filter(must=filter_conditions) if filter_conditions else None
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: client.query_points(
                collection_name="documents",
                query=text_embedding,
                using="text",
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
                score_threshold=0.0,
            ).points
        )
        return [
            {
                "chunk_id": p.payload.get("chunk_id", ""),
                "text": p.payload.get("text", ""),
                "score": float(p.score),
                "source": p.payload.get("source", ""),
                "file_name": p.payload.get("file_name", ""),
                "page": int(p.payload.get("page", 0)),
                "parent_title": p.payload.get("parent_title", ""),
                "chunk_type": p.payload.get("chunk_type", ""),
            }
            for p in results
        ]


class CitationRetriever(BaseRetriever):
    """引用检索：根据候选 SKU 列表 + query 语义匹配知识片段"""

    async def retrieve(
        self, query="", text_embedding=None, top_k=10,
        filters=None, tenant_id="default", **kw
    ) -> list[dict]:
        from app.graph.tools import retrieve_citations_tool
        sku_ids = (filters or {}).get("sku_ids", [])
        if not sku_ids:
            return []
        return await retrieve_citations_tool(
            sku_ids, query, tenant_id=tenant_id, text_embedding=text_embedding,
        )
