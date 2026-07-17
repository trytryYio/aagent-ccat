import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis

from app.config import settings

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """让 datetime 可 JSON 序列化"""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def _datetime_hook(dct: dict) -> dict:
    """JSON 反序列化时把 isoformat 字符串转回 datetime（仅 created_at）"""
    if "created_at" in dct and isinstance(dct["created_at"], str):
        try:
            dct["created_at"] = datetime.fromisoformat(dct["created_at"])
        except ValueError:
            pass
    return dct


class Session:
    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        self.created_at = datetime.now(timezone.utc)
        self.history: list[dict] = []
        self.preferences: dict = {}
        self.summary: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "history": self.history,
            "preferences": self.preferences,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        sess = cls(session_id=data.get("session_id"))
        sess.created_at = data.get("created_at", datetime.now(timezone.utc))
        sess.history = data.get("history", [])
        sess.preferences = data.get("preferences", {})
        sess.summary = data.get("summary", "")
        return sess


class SessionManager:
    """会话管理器：优先 Redis 持久化，Redis 不可用时降级到进程内存。

    多租户支持：Redis key 按 tenant_id 隔离。
    """

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._redis: Optional[redis.Redis] = None
        self._redis_available: bool = False
        self._key_prefix = settings.redis_key_prefix
        self._init_redis()

    def _init_redis(self):
        if not settings.redis_enabled:
            logger.info("Redis 已禁用，使用内存会话")
            return
        try:
            self._redis = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
            self._redis.ping()
            self._redis_available = True
            logger.info("Redis 会话连接成功 %s:%s/%s", settings.redis_host, settings.redis_port, settings.redis_db)
        except Exception as e:
            self._redis_available = False
            logger.warning("Redis 连接失败，降级为内存会话: %s", e)

    def _session_key(self, session_id: str, tenant_id: str = "default") -> str:
        """生成租户隔离的 session key。"""
        return f"{self._key_prefix}:tenant:{tenant_id}:session:{session_id}"

    def _preference_key(self, session_id: str, tenant_id: str = "default") -> str:
        """生成租户隔离的 preference key。"""
        return f"{self._key_prefix}:tenant:{tenant_id}:prefs:{session_id}"

    def _ensure_redis(self) -> bool:
        if self._redis_available and self._redis:
            return True
        # 失败一次后不再反复重连，避免每次请求都卡超时
        return False

    def _save_to_redis(self, session: Session, tenant_id: str = "default"):
        if not self._ensure_redis():
            return
        try:
            payload = json.dumps(session.to_dict(), ensure_ascii=False, cls=DateTimeEncoder)
            self._redis.setex(
                self._session_key(session.session_id, tenant_id),
                7 * 24 * 3600,  # 7 天过期
                payload,
            )
        except Exception as e:
            logger.warning("Redis 写入 session 失败: %s", e)

    def _load_from_redis(self, session_id: str, tenant_id: str = "default") -> Optional[Session]:
        if not self._ensure_redis():
            return None
        try:
            payload = self._redis.get(self._session_key(session_id, tenant_id))
            if not payload:
                return None
            data = json.loads(payload, object_hook=_datetime_hook)
            return Session.from_dict(data)
        except Exception as e:
            logger.warning("Redis 读取 session 失败: %s", e)
            return None

    def create_session(self, session_id: Optional[str] = None, tenant_id: str = "default") -> Session:
        session = Session(session_id=session_id)
        self._sessions[session.session_id] = session
        self._save_to_redis(session, tenant_id)
        return session

    def get_session(self, session_id: str, tenant_id: str = "default") -> Session | None:
        if not session_id:
            return None
        # 1) 优先本地缓存（热数据）
        if session_id in self._sessions:
            return self._sessions[session_id]
        # 2) 回 Redis 加载
        session = self._load_from_redis(session_id, tenant_id)
        if session:
            self._sessions[session_id] = session
            return session
        return None

    def get_or_create(self, session_id: str | None, tenant_id: str = "default") -> Session:
        if session_id:
            session = self.get_session(session_id, tenant_id)
            if session:
                return session
            return self.create_session(session_id, tenant_id)
        return self.create_session(tenant_id=tenant_id)

    def append_history(self, session_id: str, message: dict, tenant_id: str = "default"):
        session = self.get_or_create(session_id, tenant_id)
        session.history.append(message)
        self._save_to_redis(session, tenant_id)

    def save_preferences(self, session_id: str, preferences: dict, tenant_id: str = "default"):
        session = self.get_or_create(session_id, tenant_id)
        session.preferences.update(preferences)
        self._save_to_redis(session, tenant_id)

    def get_preferences(self, session_id: str, tenant_id: str = "default") -> dict:
        session = self.get_session(session_id, tenant_id)
        return session.preferences if session else {}


session_mgr = SessionManager()
