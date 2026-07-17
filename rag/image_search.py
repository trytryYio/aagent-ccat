from dataclasses import dataclass
from typing import List, Optional
import logging
from rag.db_client import get_qdrant_client
from qdrant_client.models import Filter, FieldCondition, MatchValue

# 配置日志
logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """
    检索结果数据类，严格遵循接口契约
    """

    product_id: str
    name: str
    price: float
    description: str
    category: str
    image_url: Optional[str]
    score: float  # 相似度分数
    source: str  # "image" | "text" | "hybrid"
    need_clarify: bool = False  # 是否需要澄清 (当 score < 0.6 时为 True)
    detail_images: Optional[str] = None  # 商品详情图片 URL 列表（逗号分隔或 JSON 数组）
    metadata: Optional[dict] = None  # 扩展字段


def search_by_image(
    image_embedding: List[float],
    top_k: int = 10,
    score_threshold: float = 0.6,
    category_filter: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[SearchResult]:
    """
    以图搜图核心逻辑
    输入: CLIP 图片向量
    返回: 按相似度排序的商品列表
    """
    client = get_qdrant_client()
    collection_name = "products"

    # 构建过滤条件 (分类 + 租户)
    filter_conditions = []
    if category_filter:
        filter_conditions.append(
            FieldCondition(key="category", match=MatchValue(value=category_filter))
        )
    if tenant_id and tenant_id != "default":
        filter_conditions.append(
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
        )
    query_filter = Filter(must=filter_conditions) if filter_conditions else None

    try:
        # 执行向量搜索
        search_results = client.query_points(
            collection_name=collection_name,
            query=image_embedding,
            using="image",  # 指定查询 image 向量空间
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,  # 需要返回商品详细信息
            score_threshold=score_threshold,
        ).points

        # 转换为 SearchResult 列表
        results = []
        for res in search_results:
            payload = res.payload
            results.append(
                SearchResult(
                    product_id=str(payload.get("product_id", "")),
                    name=payload.get("name", "未知商品"),
                    price=float(payload.get("price", 0.0)),
                    description=payload.get("description", ""),
                    category=payload.get("category", ""),
                    image_url=payload.get("image_url"),
                    score=res.score,
                    source="image",
                    need_clarify=res.score < 0.6,
                    detail_images=payload.get("detail_images", ""),
                )
            )

        logger.info(f"以图搜图完成，找到 {len(results)} 个匹配项")
        return results

    except Exception as e:
        logger.error(f"以图搜图执行失败: {str(e)}")
        return []


if __name__ == "__main__":
    # 模拟测试逻辑
    print("正在测试以图搜图接口...")
    # 实际测试需要先在数据库中插入数据，此处仅展示逻辑结构
    # mock_vector = [0.1] * 512
    # results = search_by_image(mock_vector)
    # for r in results:
    #     print(f"找到商品: {r.name}, 分数: {r.score}")
