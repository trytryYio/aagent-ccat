"""用户画像 API 路由"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/v1", tags=["profile"])


class ProfileResponse(BaseModel):
    user_id: str
    budget_range: Optional[tuple] = None
    preferred_brands: list = []
    style_tags: list = []
    total_interactions: int = 0


class DeleteResponse(BaseModel):
    ok: bool
    message: str


@router.get("/profile/{user_id}")
async def get_profile(user_id: str):
    """获取用户画像"""
    from app.memory.profile_store import RedisProfileStore
    from app.core.tenant_manager import get_redis
    
    redis = get_redis()
    store = RedisProfileStore(redis)
    profile = await store.get(user_id)
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    return ProfileResponse(
        user_id=profile.user_id,
        budget_range=profile.budget_range,
        preferred_brands=profile.preferred_brands,
        style_tags=profile.style_tags,
        total_interactions=profile.total_interactions,
    )


@router.delete("/profile/{user_id}")
async def delete_profile(user_id: str):
    """删除用户画像（隐私合规）"""
    from app.memory.profile_store import RedisProfileStore
    from app.core.tenant_manager import get_redis
    
    redis = get_redis()
    store = RedisProfileStore(redis)
    profile = await store.get(user_id)
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    await redis.delete(f"icm:profile:{user_id}")
    return DeleteResponse(ok=True, message="Profile deleted")


@router.get("/sessions/{session_id}/memory")
async def get_session_memory(session_id: str):
    """获取当前会话记忆（从 AgentState 读取）"""
    from app.core.session import get_session
    
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "session_id": session_id,
        "profile_context": session.get("profile_context", ""),
        "user_profile": session.get("user_profile"),
    }
