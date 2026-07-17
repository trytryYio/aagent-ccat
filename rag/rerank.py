"""重排序模块：支持本地 BGE-reranker + DashScope gte-rerank-v2 双后端。

默认优先本地 BGE-reranker（与 sentence-transformers 技术栈一致）；
本地模型加载失败或未配置时，自动回退到 DashScope API；
两者都失败时回退原顺序，不阻断流程。

用法:
    from rag.rerank import rerank, rerank_citations
    ranked = rerank("专业羽毛球鞋", ["无敌号羽毛球鞋...", "飞电跑鞋..."], top_n=5)
    # ranked = [{"index": 0, "score": 0.96}, ...]  按 relevance_score 降序
"""
import os
import logging
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# ---- 配置 ----
LOCAL_RERANK_MODEL = os.environ.get("LOCAL_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
DASHSCOPE_RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
DASHSCOPE_MODEL = os.environ.get("RERANK_MODEL", "gte-rerank-v2")


def _get_rerank_provider() -> str:
    """读取重排后端类型：local / dashscope / none。"""
    provider = os.environ.get("RERANK_PROVIDER", "auto").lower()
    if provider in ("local", "dashscope", "none"):
        return provider
    return "auto"  # auto：优先 local，无 DashScope key 时再降级


def _is_enabled() -> bool:
    return os.environ.get("RERANK_ENABLED", "true").lower() in ("true", "1", "yes")


def _get_api_key() -> str:
    """取 DashScope key：环境变量优先，其次读 backend/.env。"""
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if key:
        return key
    try:
        env_path = Path(__file__).resolve().parents[1] / "backend" / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DASHSCOPE_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


# ---- 本地模型单例 ----
_local_reranker: Optional[Any] = None


def _get_local_reranker() -> Optional[Any]:
    """延迟加载本地 BGE-reranker；失败返回 None。"""
    global _local_reranker
    if _local_reranker is not None:
        return _local_reranker
    try:
        from sentence_transformers import CrossEncoder
        logger.info(f"正在加载本地重排模型: {LOCAL_RERANK_MODEL}")
        device = os.environ.get("RERANK_DEVICE", "auto")
        _local_reranker = CrossEncoder(LOCAL_RERANK_MODEL, device=device, max_length=512)
        logger.info("本地重排模型加载成功")
        return _local_reranker
    except Exception as e:
        logger.warning(f"本地重排模型加载失败: {e}")
        return None


def _rerank_local(query: str, documents: List[str], top_n: int) -> List[Dict[str, Any]]:
    """使用本地 CrossEncoder 对 query-documents 打分。"""
    model = _get_local_reranker()
    if model is None:
        raise RuntimeError("本地重排模型不可用")

    pairs = [[query, doc] for doc in documents]
    scores = model.predict(pairs, convert_to_numpy=True, show_progress_bar=False)

    indexed = list(enumerate(scores))
    indexed.sort(key=lambda x: x[1], reverse=True)

    ranked = []
    for idx, score in indexed[:top_n]:
        ranked.append({"index": idx, "score": float(score)})
    logger.info(f"本地 BGE rerank 成功: {len(ranked)} 条")
    return ranked


def _rerank_dashscope(
    query: str, documents: List[str], top_n: int, model: str = DASHSCOPE_MODEL
) -> List[Dict[str, Any]]:
    """调用 DashScope rerank API。"""
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "input": {"query": query, "documents": documents},
        "parameters": {"top_n": min(top_n, len(documents)), "return_documents": False},
    }
    resp = requests.post(DASHSCOPE_RERANK_URL, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("output", {}).get("results", [])
    ranked = [{"index": r.get("index"), "score": r.get("relevance_score", 0.0)} for r in results]
    logger.info(f"DashScope rerank 成功: {len(ranked)} 条 (model={model})")
    return ranked


def rerank(
    query: str,
    documents: List[str],
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    """对 documents 相对 query 重排。

    返回 [{"index": 原始下标, "score": relevance_score}, ...] 降序，最多 top_n 个。
    失败/未配置时回退原顺序（不阻断流程）。
    """
    if not documents:
        return []
    if not _is_enabled():
        return [{"index": i, "score": 1.0} for i in range(min(top_n, len(documents)))]

    top_n = min(top_n, len(documents))
    provider = _get_rerank_provider()

    # 1. 明确指定 local
    if provider == "local":
        try:
            return _rerank_local(query, documents, top_n)
        except Exception as e:
            logger.error(f"本地 rerank 失败，回退原顺序: {e}")
            return [{"index": i, "score": 1.0} for i in range(top_n)]

    # 2. 明确指定 dashscope
    if provider == "dashscope":
        try:
            return _rerank_dashscope(query, documents, top_n)
        except Exception as e:
            logger.error(f"DashScope rerank 失败，回退原顺序: {e}")
            return [{"index": i, "score": 1.0} for i in range(top_n)]

    # 3. auto：优先本地，本地失败再试 DashScope
    try:
        return _rerank_local(query, documents, top_n)
    except Exception as local_err:
        logger.warning(f"本地 rerank 未就绪，尝试 DashScope: {local_err}")
        try:
            return _rerank_dashscope(query, documents, top_n)
        except Exception as ds_err:
            logger.error(f"DashScope rerank 也失败，回退原顺序: {ds_err}")
            return [{"index": i, "score": 1.0} for i in range(top_n)]


def rerank_citations(query: str, citations: List[Dict], top_n: int = 5) -> List[Dict]:
    """对 citations（含 content 字段）按 query 重排，返回重排后的 citations（带新 score）。"""
    if not citations:
        return citations
    docs = [str(c.get("content") or "") for c in citations]
    ranked = rerank(query, docs, top_n=top_n)
    out = []
    for r in ranked:
        idx = r["index"]
        if idx is not None and 0 <= idx < len(citations):
            c = dict(citations[idx])
            c["score"] = r["score"]
            out.append(c)
    return out


if __name__ == "__main__":
    # 自测
    logging.basicConfig(level=logging.INFO)
    q = "专业羽毛球鞋减震"
    docs = [
        "无敌号羽毛球鞋全掌碳板减震回弹专业比赛",
        "飞电6 ELITE 竞速跑鞋轻量",
        "韦德之道 篮球鞋",
        "贴地飞行 羽毛球鞋高回弹",
    ]
    print("query:", q)
    for r in rerank(q, docs, top_n=4):
        print(f"  [{r['score']:.3f}] {docs[r['index']][:30]}")
