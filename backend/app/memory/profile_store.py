"""用户画像存储层

Redis key: icm:profile:{user_id}
TTL: 30 天
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as redis_async

from app.config import settings
from app.memory.user_profile import UserProfile

logger = logging.getLogger(__name__)

# TTL: 30 天
PROFILE_TTL = 30 * 24 * 3600


def _make_key(user_id: str) -> str:
    """生成 Redis key"""
    return f"{settings.redis_key_prefix}:icm:profile:{user_id}"


class ProfileStore:
    """用户画像存储：Redis 异步存储"""

    def __init__(self):
        self._redis: Optional[redis_async.Redis] = None
        self._redis_available: bool = False
        self._init_redis()

    def _init_redis(self):
        """初始化 Redis 异步连接"""
        if not settings.redis_enabled:
            logger.info("Redis 已禁用，ProfileStore 使用空操作模式")
            return
        try:
            self._redis = redis_async.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            self._redis_available = True
            logger.info("ProfileStore Redis 连接成功")
        except Exception as e:
            self._redis_available = False
            logger.warning(f"ProfileStore Redis 连接失败: {e}")

    async def _ensure_redis(self) -> bool:
        """确保 Redis 可用"""
        if self._redis_available and self._redis:
            return True
        return False

    async def get(self, user_id: str) -> Optional[UserProfile]:
        """获取用户画像"""
        if not await self._ensure_redis():
            return None
        try:
            payload = await self._redis.get(_make_key(user_id))
            if not payload:
                return None
            data = json.loads(payload)
            return UserProfile.from_dict(data)
        except Exception as e:
            logger.error(f"读取用户画像失败 user_id={user_id}: {e}")
            return None

    async def save(self, profile: UserProfile) -> bool:
        """保存用户画像"""
        if not await self._ensure_redis():
            return False
        try:
            profile.updated_at = datetime.now(timezone.utc)
            payload = json.dumps(profile.to_dict(), ensure_ascii=False)
            await self._redis.setex(_make_key(profile.user_id), PROFILE_TTL, payload)
            logger.info(f"用户画像已保存: {profile.user_id}")
            return True
        except Exception as e:
            logger.error(f"保存用户画像失败 user_id={profile.user_id}: {e}")
            return False

    async def delete(self, user_id: str) -> bool:
        """删除用户画像"""
        if not await self._ensure_redis():
            return False
        try:
            result = await self._redis.delete(_make_key(user_id))
            return result > 0
        except Exception as e:
            logger.error(f"删除用户画像失败 user_id={user_id}: {e}")
            return False

    async def update_from_interaction(
        self,
        user_id: str,
        text: Optional[str] = None,
        viewed_sku: Optional[str] = None,
        new_session: bool = False,
    ) -> Optional[UserProfile]:
        """从交互中更新画像"""
        from app.memory.preference_extraction import extract_all

        profile = await self.get(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)

        # 从文本提取偏好
        if text:
            prefs = extract_all(text)
            if "budget_range" in prefs:
                profile.budget_range = prefs["budget_range"]
            if "preferred_brands" in prefs:
                profile.preferred_brands = _merge_unique(
                    profile.preferred_brands, prefs["preferred_brands"]
                )
            if "preferred_categories" in prefs:
                profile.preferred_categories = _merge_unique(
                    profile.preferred_categories, prefs["preferred_categories"]
                )
            if "style_tags" in prefs:
                profile.style_tags = _merge_unique(
                    profile.style_tags, prefs["style_tags"]
                )
            if "excluded_attributes" in prefs:
                profile.excluded_attributes = _merge_unique(
                    profile.excluded_attributes, prefs["excluded_attributes"]
                )
            # 记录查询
            profile.recent_queries = _slide_window(
                profile.recent_queries + [text], 20
            )

        # 记录浏览
        if viewed_sku:
            profile.recent_views = _slide_window(
                profile.recent_views + [viewed_sku], 20
            )

        # 更新计数
        profile.total_interactions += 1
        if new_session:
            profile.total_sessions += 1

        await self.save(profile)
        return profile


def _merge_unique(existing: list, incoming: list) -> list:
    """合并列表，去重"""
    seen = set()
    result = []
    for item in existing + incoming:
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _slide_window(items: list, max_size: int = 20) -> list:
    """滑动窗口"""
    if len(items) <= max_size:
        return items
    return items[-max_size:]


# 全局单例
profile_store = ProfileStore()
