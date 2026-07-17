"""租户管理 API（管理员接口，不需要 X-Tenant-ID）。"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.models import Tenant, TenantQuota
from app.core.tenant_manager import tenant_mgr

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class CreateTenantRequest(BaseModel):
    tenant_id: str
    name: str
    plan: str = "free"
    quota: Optional[TenantQuota] = None
    admin_email: Optional[str] = None


class UpdateQuotaRequest(BaseModel):
    max_requests_per_day: Optional[int] = None
    max_tokens_per_month: Optional[int] = None
    max_storage_mb: Optional[int] = None


@router.get("/tenants")
async def list_tenants():
    """列出所有租户。"""
    tenants = tenant_mgr.list_tenants()
    return {"code": 0, "data": [t.model_dump() for t in tenants]}


@router.post("/tenants")
async def create_tenant(req: CreateTenantRequest):
    """创建新租户。"""
    # 检查是否已存在
    existing = tenant_mgr.get_tenant(req.tenant_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"租户 {req.tenant_id} 已存在")

    tenant = Tenant(
        tenant_id=req.tenant_id,
        name=req.name,
        plan=req.plan,
        quota=req.quota or TenantQuota(),
        admin_email=req.admin_email,
    )
    success = tenant_mgr.create_tenant(tenant)
    if not success:
        raise HTTPException(status_code=500, detail="创建租户失败")

    return {"code": 0, "message": "租户创建成功", "data": tenant.model_dump()}


@router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str):
    """查询单个租户详情。"""
    tenant = tenant_mgr.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"租户 {tenant_id} 不存在")
    return {"code": 0, "data": tenant.model_dump()}


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str):
    """删除租户（软删除，改状态为 deleted）。"""
    tenant = tenant_mgr.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"租户 {tenant_id} 不存在")

    tenant.status = "deleted"
    tenant_mgr.create_tenant(tenant)  # 更新
    return {"code": 0, "message": f"租户 {tenant_id} 已删除"}


@router.put("/tenants/{tenant_id}/quota")
async def update_quota(tenant_id: str, req: UpdateQuotaRequest):
    """更新租户配额。"""
    tenant = tenant_mgr.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"租户 {tenant_id} 不存在")

    # 部分更新
    if req.max_requests_per_day is not None:
        tenant.quota.max_requests_per_day = req.max_requests_per_day
    if req.max_tokens_per_month is not None:
        tenant.quota.max_tokens_per_month = req.max_tokens_per_month
    if req.max_storage_mb is not None:
        tenant.quota.max_storage_mb = req.max_storage_mb

    tenant_mgr.create_tenant(tenant)
    return {"code": 0, "message": "配额更新成功", "data": tenant.model_dump()}


@router.get("/tenants/{tenant_id}/usage")
async def get_usage(tenant_id: str):
    """查询租户当前用量。"""
    tenant = tenant_mgr.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"租户 {tenant_id} 不存在")

    usage = tenant_mgr.get_usage(tenant_id)
    return {"code": 0, "data": usage.model_dump()}
