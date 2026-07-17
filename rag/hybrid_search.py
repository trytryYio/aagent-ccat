from typing import List, Optional, Dict
import logging
from rag.image_search import SearchResult, search_by_image
from rag.text_retrieval import search_by_text
from rag.rerank import rerank

# 配置日志
logger = logging.getLogger(__name__)

# ====== 快速路径配置（支持环境变量覆盖）======
import os as _os
FAST_PATH_ENABLED = _os.environ.get("FAST_PATH_ENABLED", "true").lower() in ("true", "1", "yes")
FAST_PATH_IMAGE_THRESHOLD = float(_os.environ.get("FAST_PATH_IMAGE_THRESHOLD", "0.85"))
FAST_PATH_TEXT_THRESHOLD = float(_os.environ.get("FAST_PATH_TEXT_THRESHOLD", "0.75"))
FAST_PATH_HYBRID_IMAGE_THRESHOLD = float(_os.environ.get("FAST_PATH_HYBRID_IMAGE_THRESHOLD", "0.90"))

# ====== 快速路径统计（供评测脚本读取）======
_fast_path_stats = {"total": 0, "fast_path": 0}

def get_fast_path_stats():
    """返回快速路径统计（供 eval 脚本调用）。"""
    d = dict(_fast_path_stats)
    d["ratio"] = (d["fast_path"] / d["total"]) if d["total"] > 0 else 0.0
    return d

def _record_fast_path(enabled: bool):
    _fast_path_stats["total"] += 1
    if enabled:
        _fast_path_stats["fast_path"] += 1


def _candidate_to_text(candidate: SearchResult) -> str:
    """把候选商品转成 reranker 可读的文本。"""
    parts = [candidate.name or "", candidate.description or "", candidate.category or ""]
    if candidate.price is not None:
        parts.append(f"价格:{candidate.price}")
    return " ".join(p for p in parts if p)


def hybrid_search(
    image_embedding=None,
    text_embedding=None,
    query: Optional[str] = None,
    top_k: int = 10,
    rrf_k: int = 60,
    rerank_enabled: bool = True,
    tenant_id: Optional[str] = None,
    qdrant_filter=None,
) -> List[SearchResult]:
    """
    图文混合搜索逻辑 (基于 RRF 算法 + 可选 cross-encoder rerank)

    快速路径（跳过 Rerank，省 ~800ms）：
    - 纯图片 + CLIP > 0.85 → 直接返回
    - 纯文本 + BGE-M3 > 0.75 → 直接返回
    - 图文混合 + CLIP > 0.90 → 只做 RRF，跳过 Rerank
    """

    recall_k = max(top_k * 2, 20)

    image_results = []
    if image_embedding:
        image_results = search_by_image(image_embedding, top_k=recall_k, tenant_id=tenant_id)

    text_results = []
    if text_embedding:
        text_results = search_by_text(
            text_embedding, top_k=recall_k, tenant_id=tenant_id,
            qdrant_filter=qdrant_filter,
        )

    # ====== 快速路径 1：纯图片 + 高置信 ======
    if image_results and not text_results:
        top_image_score = float(image_results[0].score)
        if FAST_PATH_ENABLED and top_image_score >= FAST_PATH_IMAGE_THRESHOLD:
            logger.info("[FastPath] 图片高置信(%.3f)，跳过 Rerank", top_image_score)
            _record_fast_path(True)
            return image_results[:top_k]

    # ====== 快速路径 2：纯文本 + 高置信 ======
    if text_results and not image_results:
        top_text_score = float(text_results[0].score)
        if FAST_PATH_ENABLED and top_text_score >= FAST_PATH_TEXT_THRESHOLD:
            logger.info("[FastPath] 文本高置信(%.3f)，跳过 Rerank", top_text_score)
            _record_fast_path(True)
            return text_results[:top_k]

    # ====== 快速路径 3：图文混合 + 图片信号极强 ======
    skip_rerank = False
    if image_results and text_results:
        top_image_score = float(image_results[0].score)
        if FAST_PATH_ENABLED and top_image_score >= FAST_PATH_HYBRID_IMAGE_THRESHOLD:
            skip_rerank = True
            logger.info("[FastPath] 图文混合 + 图片极强(%.3f)，跳过 Rerank", top_image_score)
            _record_fast_path(True)

    # ====== RRF 融合 ======
    if not image_results:
        fused = text_results
    elif not text_results:
        fused = image_results
    else:
        scores: Dict[str, float] = {}
        products: Dict[str, SearchResult] = {}
        is_confident: Dict[str, bool] = {}
        image_scores: Dict[str, float] = {}
        text_scores: Dict[str, float] = {}
        original_image_scores = {res.product_id: float(res.score) for res in image_results}

        for rank, res in enumerate(image_results, start=1):
            pid = res.product_id
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (rrf_k + rank)
            image_scores[pid] = float(res.score)
            products[pid] = res
            if not res.need_clarify:
                is_confident[pid] = True

        for rank, res in enumerate(text_results, start=1):
            pid = res.product_id
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (rrf_k + rank)
            text_scores[pid] = float(res.score)
            if pid not in products:
                products[pid] = res
            if not res.need_clarify:
                is_confident[pid] = True

        sorted_pids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        fused = []
        for pid in sorted_pids:
            res = products[pid]
            rrf_score = scores[pid]
            res.score = rrf_score
            res.source = "hybrid"
            res.metadata = {
                **(res.metadata or {}),
                "rrf_score": rrf_score,
                "image_score": image_scores.get(pid),
                "text_score": text_scores.get(pid),
                "confidence_score": max(
                    score for score in (image_scores.get(pid), text_scores.get(pid))
                    if score is not None
                ),
            }
            res.need_clarify = not is_confident.get(pid, False)
            fused.append(res)

    # ====== 快速路径：跳过 Rerank ======
    if skip_rerank:
        fused = fused[:top_k]
        logger.info("[FastPath] 返回 RRF 结果 %d 个（跳过 Rerank）", len(fused))
        return fused

    # ====== Rerank 精排 ======
    _record_fast_path(False)
    if rerank_enabled and query and query.strip() and len(fused) > 1:
        try:
            docs = [_candidate_to_text(c) for c in fused]
            ranked = rerank(query.strip(), docs, top_n=top_k)
            reranked = []
            for r in ranked:
                idx = r.get("index")
                if idx is None or idx < 0 or idx >= len(fused):
                    continue
                candidate = fused[idx]
                rerank_score = float(r.get("score", candidate.score))
                candidate.score = rerank_score
                candidate.source = "hybrid_rerank"
                candidate.metadata = {**(candidate.metadata or {}), "rerank_score": rerank_score}
                reranked.append(candidate)
            seen_idx = {r.get("index") for r in ranked if r.get("index") is not None}
            for i, c in enumerate(fused):
                if i not in seen_idx:
                    reranked.append(c)
            if image_embedding:
                combined: Dict[str, SearchResult] = {
                    candidate.product_id: candidate for candidate in reranked
                }
                for candidate in image_results:
                    original_image_score = original_image_scores[candidate.product_id]
                    existing = combined.get(candidate.product_id)
                    if existing:
                        existing.metadata = {
                            **(existing.metadata or {}),
                            "image_score": original_image_score,
                            "confidence_score": max(
                                original_image_score,
                                float((existing.metadata or {}).get("text_score") or 0),
                            ),
                        }
                        existing.need_clarify = existing.need_clarify and original_image_score < 0.6
                    else:
                        candidate.metadata = {
                            **(candidate.metadata or {}),
                            "rrf_score": float(candidate.score),
                            "image_score": original_image_score,
                            "confidence_score": original_image_score,
                        }
                        candidate.need_clarify = original_image_score < 0.6
                        combined[candidate.product_id] = candidate
                fused = sorted(
                    combined.values(),
                    key=lambda c: (
                        (c.metadata or {}).get("confidence_score", c.score),
                        (c.metadata or {}).get("rerank_score", 0),
                    ),
                    reverse=True,
                )[:top_k]
                for candidate in fused:
                    candidate.score = float(
                        (candidate.metadata or {}).get("confidence_score", candidate.score)
                    )
            else:
                fused = reranked[:top_k]
            logger.info(f"混合搜索 + rerank 完成，返回 {len(fused)} 个结果")
        except Exception as e:
            logger.warning(f"候选商品 rerank 失败，回退到 RRF 结果: {e}")
            fused = fused[:top_k]
    else:
        fused = fused[:top_k]

    return fused


if __name__ == "__main__":
    print("正在测试混合搜索接口...")


