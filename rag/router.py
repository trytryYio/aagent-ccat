"""查询路由器：根据输入类型自动选择最优检索器

路由规则：
- 有图+有文 → HybridRetriever
- 仅图 → ImageRetriever
- 仅文 → TextRetriever
- 需要引用 → CitationRetriever（由 retrieve_citations_node 直接调用）
"""

import logging
from typing import Optional

from rag.retrievers import BaseRetriever, HybridRetriever, ImageRetriever, TextRetriever

logger = logging.getLogger(__name__)


class QueryRouter:
    """查询路由器：注册检索器 + 按输入状态自动路由"""

    def __init__(self):
        self._retrievers: dict[str, BaseRetriever] = {}
        self._register_defaults()

    def _register_defaults(self):
        """注册默认检索器"""
        self.register("hybrid", HybridRetriever())
        self.register("image", ImageRetriever())
        self.register("text", TextRetriever())

    def register(self, name: str, retriever: BaseRetriever):
        self._retrievers[name] = retriever

    def route(
        self,
        image_embedding: Optional[list[float]] = None,
        text_embedding: Optional[list[float]] = None,
    ) -> tuple[BaseRetriever, str]:
        """根据输入向量自动选择检索器。返回 (retriever, retriever_name)。"""
        if image_embedding and text_embedding:
            name = "hybrid"
        elif image_embedding:
            name = "image"
        elif text_embedding:
            name = "text"
        else:
            name = "text"  # 兜底

        retriever = self._retrievers.get(name)
        if not retriever:
            raise ValueError(f"未注册检索器: {name}")

        logger.info("[Router] → %s (has_image=%s, has_text=%s)",
                     name, image_embedding is not None, text_embedding is not None)
        return retriever, name


# 全局单例
_router: Optional[QueryRouter] = None


def get_query_router() -> QueryRouter:
    global _router
    if _router is None:
        _router = QueryRouter()
    return _router
