"""多租户中间件：从请求头提取 X-Tenant-ID，校验租户状态与配额。

如果没有 X-Tenant-ID，使用默认租户（向后兼容）。
如果租户不存在或配额超限，返回 403/429。
"""

import logging
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.tenant_manager import tenant_mgr, DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

# 不需要租户校验的路径（健康检查、管理接口等）
_EXEMPT_PATHS = {
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/admin/tenants",  # 管理接口本身不需要租户上下文
}


class TenantMiddleware(BaseHTTPMiddleware):
    """多租户中间件：提取租户 → 校验 → 注入 request.state。"""

    async def dispatch(self, request: Request, call_next):
        # 1. 豁免路径直接放行
        if request.url.path in _EXEMPT_PATHS:
            request.state.tenant_id = DEFAULT_TENANT_ID
            return await call_next(request)

        # 2. 静态资源放行
        if request.url.path.startswith("/api/v1/images/"):
            request.state.tenant_id = DEFAULT_TENANT_ID
            return await call_next(request)

        # 3. 提取 X-Tenant-ID
        tenant_id = request.headers.get("X-Tenant-ID", DEFAULT_TENANT_ID)

        # 4. 校验租户是否存在
        tenant = tenant_mgr.get_tenant(tenant_id)
        if not tenant:
            logger.warning(f"未知租户: {tenant_id}")
            return JSONResponse(
                status_code=403,
                content={"error": "tenant_not_found", "message": f"租户 {tenant_id} 不存在"},
            )

        # 5. 校验租户状态
        if tenant.status != "active":
            return JSONResponse(
                status_code=403,
                content={"error": "tenant_suspended", "message": f"租户 {tenant_id} 已停用"},
            )

        # 6. 校验配额
        allowed, reason = tenant_mgr.check_quota(tenant_id)
        if not allowed:
            logger.warning(f"租户 {tenant_id} 配额超限: {reason}")
            return JSONResponse(
                status_code=429,
                content={"error": "quota_exceeded", "message": reason},
            )

        # 7. 注入 tenant_id 到 request.state
        request.state.tenant_id = tenant_id

        # 8. 记录请求数（异步，不阻塞响应）
        tenant_mgr.incr_request_count(tenant_id)

        # 9. 继续处理请求
        response = await call_next(request)

        # 10. 在响应头里加上租户标识（方便调试）
        response.headers["X-Tenant-ID"] = tenant_id

        return response
