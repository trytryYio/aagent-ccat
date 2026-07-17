"""可观测性：请求日志 + 异常处理 + 上下文日志字段"""

import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """注入链路字段（session/message_id/耗时）并记录结构化日志。"""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        # 优先复用请求携带的 request_id；没有则生成一个
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id

        response = await call_next(request)

        latency_ms = (time.time() - start) * 1000
        path = request.url.path
        method = request.method
        status = response.status_code

        # 只记录 API 调用，过滤静态资源
        if path.startswith("/api/"):
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status": status,
                    "latency_ms": round(latency_ms, 2),
                },
            )
        return response


async def http_exception_handler(request: Request, exc):
    """把 FastAPI 的 HTTPException 也格式化成统一 JSON 响应。"""
    status = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", "未知错误")
    if isinstance(detail, dict):
        body = {"code": detail.get("code", "HTTP_ERROR"), "message": detail.get("message", str(detail))}
    else:
        body = {"code": "HTTP_ERROR", "message": str(detail)}
    logger.warning(
        "http_exception",
        extra={"request_id": getattr(request.state, "request_id", ""), "status": status, "detail": body},
    )
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content=body)


async def general_exception_handler(request: Request, exc):
    """兜底未知异常：隐藏细节返回 JSON，详细堆栈写日志。"""
    import traceback

    request_id = getattr(request.state, "request_id", "")
    logger.exception(
        "unexpected_exception",
        extra={"request_id": request_id, "error": str(exc), "traceback": traceback.format_exc()},
    )
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": f"服务器内部错误，请求 ID: {request_id}"},
    )


def setup_logging():
    """配置结构化日志格式（保留时间/级别/消息，单行 JSON 方便后续采集）。"""
    import json

    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log = {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": getattr(record, "request_id", ""),
            }
            # 合并 extra 字段
            for key in ("method", "path", "status", "latency_ms", "detail", "error"):
                if hasattr(record, key):
                    log[key] = getattr(record, key)
            return json.dumps(log, ensure_ascii=False)

    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    if root.handlers:
        handler = root.handlers[0]
    else:
        handler = logging.StreamHandler()
        root.addHandler(handler)
    handler.setFormatter(JsonFormatter())
