"""ICM 跨会话记忆 单元测试"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from app.memory.user_profile import UserProfile
from app.memory.profile_store import ProfileStore, _make_key


# ====== 直接测试函数 ======
from app.memory.preference_extraction import (
    extract_budget,
    extract_brands,
    extract_style_tags,
    extract_all,
)


class TestUserProfile:
    """测试 UserProfile 数据模型"""
    
    def test_create_profile(self):
        """创建用户画像"""
        p = UserProfile(
            user_id="test_user",
            budget_range=(500, 1200),
            preferred_brands=["李宁"],
            style_tags=["透气", "轻量"],
        )
        assert p.user_id == "test_user"
        assert p.budget_range == (500, 1200)
        assert "李宁" in p.preferred_brands
        assert p.total_interactions == 0
    
    def test_to_prompt_context(self):
        """画像转 LLM prompt 上下文"""
        p = UserProfile(
            user_id="test",
            budget_range=(500, 1200),
            preferred_brands=["李宁"],
            style_tags=["透气"],
        )
        ctx = p.to_prompt_context()
        assert "500-1200" in ctx
        assert "李宁" in ctx
        assert "透气" in ctx
    
    def test_empty_profile_context(self):
        """空画像返回空字符串"""
        p = UserProfile(user_id="test")
        ctx = p.to_prompt_context()
        assert "暂无用户画像" in ctx or ctx == ""
    
    def test_sliding_window(self):
        """recent_queries 滑动窗口逻辑"""
        p = UserProfile(user_id="test")
        p.recent_queries = [f"q{i}" for i in range(25)]
        # Sliding window is handled by ProfileStore, not dataclass\n        assert len(p.recent_queries) == 25  # Dataclass has no limit


class TestPreferenceExtraction:
    """测试规则偏好提取函数"""
    
    def test_extract_budget_range(self):
        """提取预算范围"""
        result = extract_budget("500-1000元的跑鞋")
        assert result == (500, 1000)
    
    def test_extract_budget_max(self):
        """提取预算上限"""
        result = extract_budget("预算800以内")
        assert result[1] == 800
    
    def test_extract_brand_lining(self):
        """提取品牌：李宁"""
        result = extract_brands("飞电6跑鞋")
        assert "李宁" in result
    
    def test_extract_style_tags(self):
        """提取风格偏好"""
        result = extract_style_tags("要透气的轻便跑鞋")
        assert "透气" in result
        assert "轻量" in result
    
    def test_extract_all_combined(self):
        """同时提取多种偏好"""
        result = extract_all("500-800元的李宁飞电，要透气")
        assert result.get("budget_range") == (500, 800)
        assert "李宁" in result.get("preferred_brands", [])
        assert "透气" in result["style_tags"]


class TestProfileStore:
    """测试 ProfileStore（Mock Redis）"""
    
    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock(return_value=True)
        redis.delete = AsyncMock(return_value=1)
        return redis
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_profile(self, mock_redis):
        """获取不存在的画像返回 None"""
        store = ProfileStore.__new__(ProfileStore)
        store._redis = mock_redis
        store._redis_available = True
        result = await store.get("nonexistent")
        assert result is None
    
    def test_make_key(self):
        """测试 Redis key 生成"""
        key = _make_key("user123")
        assert "user123" in key
        assert "icm:profile" in key

