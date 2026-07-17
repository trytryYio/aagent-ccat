"""租户管理器：CRUD + 用量计数 + 配额检查。

租户元数据存 Redis Hash：tenant:meta:{tenant_id}
用量计数存 Redis String + TTL：
  - tenant:usage:req:{tenant_id}:{YYYY-MM-DD}  （每日请求数，24h TTL）
  - tenant:usage:tok:{tenant_id}:{YYYY-MM}      （每月 token 数，31 天 TTL）
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis

from app.config import settings
from app.models import Tenant, TenantQuota, TenantUsage

logger = logging.getLogger(__name__)

# 默认租户（向后兼容：没有 X-Tenant-ID 时走这个）
DEFAULT_TENANT_ID = "default"


class TenantManager:
    """租户管理：Redis 存储 + 配额检查。"""

    def __init__(self):
        self._redis: Optional[redis.Redis] = None
        self._redis_available: bool = False
        self._init_redis()

        # 启动时确保默认租户存在
        if self._redis_available:
            self._ensure_default_tenant()

    def _init_redis(self):
        """复用 session.py 的 Redis 连接逻辑。"""
        try:
            self._redis = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            self._redis.ping()
            self._redis_available = True
            logger.info("TenantManager Redis 连接成功")
        except Exception as e:
            self._redis_available = False
            logger.warning(f"TenantManager Redis 连接失败: {e}")

    def _meta_key(self, tenant_id: str) -> str:
        return f"{settings.redis_key_prefix}:tenant:meta:{tenant_id}"

    def _usage_req_key(self, tenant_id: str, date_str: str) -> str:
        return f"{settings.redis_key_prefix}:tenant:usage:req:{tenant_id}:{date_str}"

    def _usage_tok_key(self, tenant_id: str, month_str: str) -> str:
        return f"{settings.redis_key_prefix}:tenant:usage:tok:{tenant_id}:{month_str}"

    def _ensure_default_tenant(self):
        """确保默认租户存在（首次启动时自动创建）。"""
        if not self._redis_available:
            return
        key = self._meta_key(DEFAULT_TENANT_ID)
        if not self._redis.exists(key):
            default = Tenant(
                tenant_id=DEFAULT_TENANT_ID,
                name="默认租户",
                plan="free",
                quota=TenantQuota(
                    max_requests_per_day=10000,
                    max_tokens_per_month=10_000_000,
                    max_storage_mb=1000,
                ),
            )
            self.create_tenant(default)
            logger.info(f"已创建默认租户: {DEFAULT_TENANT_ID}")

    # ---- CRUD ----

    def create_tenant(self, tenant: Tenant) -> bool:
        if not self._redis_available:
            logger.warning("Redis 不可用，无法创建租户")
            return False
        try:
            payload = tenant.model_dump_json()
            self._redis.set(self._meta_key(tenant.tenant_id), payload)
            logger.info(f"租户已创建/更新: {tenant.tenant_id}")
            return True
        except Exception as e:
            logger.error(f"创建租户失败: {e}")
            return False

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        if not self._redis_available:
            return None
        try:
            payload = self._redis.get(self._meta_key(tenant_id))
            if not payload:
                return None
            return Tenant.model_validate_json(payload)
        except Exception as e:
            logger.error(f"读取租户失败: {e}")
            return None

    def delete_tenant(self, tenant_id: str) -> bool:
        if not self._redis_available:
            return False
        try:
            self._redis.delete(self._meta_key(tenant_id))
            return True
        except Exception as e:
            logger.error(f"删除租户失败: {e}")
            return False

    def list_tenants(self) -> list[Tenant]:
        if not self._redis_available:
            return []
        try:
            pattern = f"{settings.redis_key_prefix}:tenant:meta:*"
            keys = self._redis.keys(pattern)
            tenants = []
            for key in keys:
                payload = self._redis.get(key)
                if payload:
                    tenants.append(Tenant.model_validate_json(payload))
            return tenants
        except Exception as e:
            logger.error(f"列出租户失败: {e}")
            return []

    # ---- 用量计数 ----

    def incr_request_count(self, tenant_id: str) -> int:
        """每日请求数 +1，返回当前计数。"""
        if not self._redis_available:
            return 0
        try:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            key = self._usage_req_key(tenant_id, date_str)
            count = self._redis.incr(key)
            if count == 1:
                self._redis.expire(key, 24 * 3600)  # 24h 自动过期
            return count
        except Exception as e:
            logger.warning(f"请求计数失败: {e}")
            return 0

    def incr_token_count(self, tenant_id: str, tokens: int) -> int:
        """每月 token 数 +N，返回当前计数。"""
        if not self._redis_available:
            return 0
        try:
            month_str = datetime.now(timezone.utc).strftime("%Y-%m")
            key = self._usage_tok_key(tenant_id, month_str)
            count = self._redis.incrby(key, tokens)
            if count == tokens:
                self._redis.expire(key, 31 * 24 * 3600)  # 31 天自动过期
            return count
        except Exception as e:
            logger.warning(f"Token 计数失败: {e}")
            return 0

    def get_usage(self, tenant_id: str) -> TenantUsage:
        """查询当前用量。"""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month_str = datetime.now(timezone.utc).strftime("%Y-%m")
        req_key = self._usage_req_key(tenant_id, date_str)
        tok_key = self._usage_tok_key(tenant_id, month_str)

        requests_today = 0
        tokens_this_month = 0
        if self._redis_available:
            requests_today = int(self._redis.get(req_key) or 0)
            tokens_this_month = int(self._redis.get(tok_key) or 0)

        return TenantUsage(
            tenant_id=tenant_id,
            date=date_str,
            requests_today=requests_today,
            tokens_this_month=tokens_this_month,
        )

    # ---- 配额检查 ----

    def check_quota(self, tenant_id: str) -> tuple[bool, str]:
        """检查租户配额是否超限。
        返回 (is_allowed, reason)。
        """
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False, f"租户 {tenant_id} 不存在"
        if tenant.status != "active":
            return False, f"租户 {tenant_id} 已停用（{tenant.status}）"

        usage = self.get_usage(tenant_id)

        if usage.requests_today >= tenant.quota.max_requests_per_day:
            return False, f"今日请求数已达上限（{tenant.quota.max_requests_per_day}）"
        if usage.tokens_this_month >= tenant.quota.max_tokens_per_month:
            return False, f"本月 token 已达上限（{tenant.quota.max_tokens_per_month}）"

        return True, "ok"


# 全局单例
tenant_mgr = TenantManager()
