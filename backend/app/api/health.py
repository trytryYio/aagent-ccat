from fastapi import APIRouter
import datetime
import logging

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/v1/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@router.get("/api/v1/ready")
async def ready():
    """就绪探针：检查核心依赖是否可用。"""
    checks = {}
    status_code = 200

    # Redis
    try:
        from app.core.session import session_mgr
        if session_mgr._redis_available:
            session_mgr._redis.ping()
            checks["redis"] = "ok"
        else:
            checks["redis"] = "disabled_or_unavailable"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        status_code = 503

    # Qdrant
    try:
        from rag.db_client import get_qdrant_client
        client = get_qdrant_client()
        if settings.qdrant_url and settings.qdrant_api_key:
            # 云端模式：初始化成功即认为配置可用；get_collections 可能因路径前缀/API 权限返回 404
            client.get_collections()
        else:
            client.get_collections()
        checks["qdrant"] = "ok"
    except Exception as e:
        err_str = str(e)
        # 兼容云端 Qdrant Cloud：404 页面未找到视为可达，但集合接口可能带前缀
        if "404" in err_str or "Not Found" in err_str:
            checks["qdrant"] = "reachable(404)"
        else:
            checks["qdrant"] = f"error: {e}"
            status_code = 503

    # LLM（不真正调用，只看配置是否完整）
    try:
        if not settings.llm_api_key or not settings.llm_model:
            checks["llm"] = "error: missing api_key or model"
            status_code = 503
        else:
            checks["llm"] = "configured"
    except Exception as e:
        checks["llm"] = f"error: {e}"
        status_code = 503

    body = {
        "status": "ready" if status_code == 200 else "not_ready",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "checks": checks,
    }
    from fastapi.responses import JSONResponse

    return JSONResponse(content=body, status_code=status_code)
