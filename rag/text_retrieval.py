from typing import Dict, List, Optional
import logging

from qdrant_client.models import FieldCondition, Filter, MatchValue

from rag.db_client import get_qdrant_client
from rag.image_search import SearchResult

logger = logging.getLogger(__name__)


def search_by_text(
    text_embedding: List[float],
    top_k: int = 10,
    score_threshold: float = 0.5,
    category_filter: Optional[str] = None,
    tenant_id: Optional[str] = None,
    qdrant_filter=None,
    collection_name: str = "products",
) -> List[SearchResult]:
    client = get_qdrant_client()

    # 优先使用传入的完整 Qdrant filter（来自 slot_filling.get_qdrant_filter）
    if qdrant_filter is not None:
        query_filter = qdrant_filter
    else:
        # 回退：仅 tenant_id 做硬过滤
        filter_conditions = []
        if tenant_id and tenant_id != "default":
            filter_conditions.append(
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
            )
        query_filter = Filter(must=filter_conditions) if filter_conditions else None

    try:
        # 先不做 category 硬过滤，多取一些结果再软过滤
        search_results = client.query_points(
            collection_name=collection_name,
            query=text_embedding,
            using="text",
            query_filter=query_filter,
            limit=top_k * 3 if category_filter else top_k,
            with_payload=True,
            score_threshold=score_threshold,
        ).points

        results: List[SearchResult] = []
        for res in search_results:
            payload = res.payload
            # 软过滤 category：如果指定了 category_filter，检查数据中的 category 是否包含该关键词
            if category_filter:
                data_category = str(payload.get("category", ""))
                if category_filter not in data_category:
                    continue
            results.append(
                SearchResult(
                    product_id=str(payload.get("product_id", "")),
                    name=payload.get("name", "unknown"),
                    price=float(payload.get("price", 0.0)),
                    description=payload.get("description", ""),
                    category=payload.get("category", ""),
                    image_url=payload.get("image_url"),
                    score=res.score,
                    source="text",
                    need_clarify=res.score < 0.6,
                    detail_images=payload.get("detail_images", ""),
                )
            )

        # 如果 category 软过滤后结果太少，放宽限制
        if category_filter and len(results) < 3:
            logger.warning(f"category 软过滤后结果太少({len(results)})，放宽限制")
            for res in search_results:
                if len(results) >= top_k:
                    break
                payload = res.payload
                already_in = any(r.product_id == str(payload.get("product_id", "")) for r in results)
                if not already_in:
                    results.append(
                        SearchResult(
                            product_id=str(payload.get("product_id", "")),
                            name=payload.get("name", "unknown"),
                            price=float(payload.get("price", 0.0)),
                            description=payload.get("description", ""),
                            category=payload.get("category", ""),
                            image_url=payload.get("image_url"),
                            score=res.score,
                            source="text",
                            need_clarify=res.score < 0.6,
                            detail_images=payload.get("detail_images", ""),
                        )
                    )

        logger.info(f"文本检索完成，找到 {len(results)} 个匹配项")
        return results[:top_k]
    except Exception as e:
        logger.error(f"文本检索执行失败: {str(e)}")
        return []


def get_citations_by_sku(sku_id: str) -> List[Dict]:
    client = get_qdrant_client()
    collection_name = "citations"

    try:
        response = client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[FieldCondition(key="product_id", match=MatchValue(value=sku_id))]
            ),
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        points = response[0]
        citations = [p.payload for p in points]
        logger.info(f"成功获取 SKU {sku_id} 的 {len(citations)} 条引用片段")
        return citations
    except Exception as e:
        logger.error(f"查询 SKU 引用失败: {str(e)}")
        return []


def get_citations(
    text_embedding: List[float], product_ids: List[str], top_k: int = 5, query: str = "", tenant_id: Optional[str] = None
) -> List[Dict]:
    client = get_qdrant_client()
    collection_name = "citations"

    if not text_embedding or not product_ids:
        return []

    # 有 query 时多检索候选，交给千问 rerank 精选
    fetch_k = top_k * 3 if query else top_k

    # 构造 filter：product_ids + tenant_id
    filter_conditions = [
        Filter(
            should=[
                FieldCondition(key="product_id", match=MatchValue(value=pid))
                for pid in product_ids
            ]
        )
    ]
    if tenant_id and tenant_id != "default":
        filter_conditions.append(
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
        )
    query_filter = Filter(must=filter_conditions)

    query_attempts = [
        {
            "collection_name": collection_name,
            "query": text_embedding,
            "query_filter": query_filter,
            "limit": fetch_k,
            "with_payload": True,
        },
        {
            "collection_name": collection_name,
            "query": text_embedding,
            "using": "text",
            "query_filter": query_filter,
            "limit": fetch_k,
            "with_payload": True,
        },
    ]

    try:
        search_results = []
        last_error = None
        for query_kwargs in query_attempts:
            try:
                search_results = client.query_points(**query_kwargs).points
                last_error = None
                break
            except Exception as query_error:
                last_error = query_error

        if last_error is not None:
            raise last_error

        citations = []
        for res in search_results:
            citations.append(
                {
                    "product_id": res.payload.get("product_id"),
                    "tag": res.payload.get("tag"),
                    "content": res.payload.get("content"),
                    "score": res.score,
                }
            )

        # 千问 rerank 精选（有 query 时）
        if query and citations:
            try:
                from rag.rerank import rerank_citations
                citations = rerank_citations(query, citations, top_n=top_k)
                logger.info(f"千问 rerank 后保留 {len(citations)} 条引用")
            except Exception as re:
                logger.warning(f"rerank 失败，回退原始检索顺序: {re}")
                citations = citations[:top_k]
        else:
            citations = citations[:top_k]

        # 兜底：每个候选商品至少保留 1 条引用，避免 Context Recall 过低
        # 策略：先收集 rerank 结果中每个 product_id 的 top-1，剩余名额按原顺序填充
        if product_ids and len(citations) < top_k * 2:
            per_product_top1 = {}
            for c in citations:
                pid = c.get("product_id")
                if pid and pid not in per_product_top1:
                    per_product_top1[pid] = c

            # 对没有覆盖到的候选商品，补充其 top-1 引用
            covered_pids = set(per_product_top1.keys())
            missing_pids = [pid for pid in product_ids if pid not in covered_pids]
            if missing_pids:
                # 构建已有 product_id 集合，用于去重
                existing_keys = {(c.get("product_id"), c.get("content", "")[:50]) for c in citations}
                for pid in missing_pids:
                    for item in get_citations_by_sku(pid):
                        key = (item.get("product_id"), (item.get("content") or "")[:50])
                        if key not in existing_keys:
                            citations.append({
                                "product_id": item.get("product_id"),
                                "tag": item.get("tag"),
                                "content": item.get("content"),
                                "score": 0.0,  # 兜底引用给低分
                            })
                            existing_keys.add(key)
                            break

        logger.info(f"成功从候选商品中检索到 {len(citations)} 条相关知识引用")
        return citations
    except Exception as e:
        logger.error(f"检索知识引用失败: {str(e)}")
        fallback_citations: List[Dict] = []
        for sku_id in product_ids[:top_k]:
            for item in get_citations_by_sku(sku_id):
                fallback_citations.append(
                    {
                        "product_id": item.get("product_id"),
                        "tag": item.get("tag"),
                        "content": item.get("content"),
                        "score": 0.0,
                    }
                )
                if len(fallback_citations) >= top_k:
                    return fallback_citations
        return fallback_citations
