"""四层记忆系统：工作记忆 + 情景记忆 + 语义记忆 + 感知记忆"""

import json
import logging
from typing import Optional

from app.core.session import Session, session_mgr
from app.agent.memory import extract_preferences

logger = logging.getLogger(__name__)

# 长对话控制阈值
MAX_HISTORY_TURNS = 10      # 超过此轮数触发摘要
RECENT_TURNS_KEEP = 6       # 保留最近 N 轮原消息


class WorkingMemory:
    """L1: 工作记忆 — 当前 graph run 的瞬时状态（AgentState 本身）"""
    @staticmethod
    def init_state(session_id: str, image_id: Optional[str] = None, text: Optional[str] = None) -> dict:
        return {
            "image_id": image_id,
            "text": text or "",
            "session_id": session_id,
            "candidates": [],
            "citations": [],
            "image_embedding": None,
            "text_embedding": None,
            "need_clarify": False,
            "clarify_question": None,
            "clarify_answered": False,
            "intent": "",
            "plan": [],
            "plan_step": 0,
            "task_complete": False,
            "reflection_passed": False,
            "reflection_feedback": None,
            "generation_done": False,
            "final_answer": None,
            "usages": [],
            "rewritten_query": None,
            "preferences": {},
            "history": [],
            "messages": [],
        }


class EpisodicMemory:
    """L2: 情景记忆 — 会话历史（Redis）"""

    @staticmethod
    def get_session(session_id: str, tenant_id: str = "default") -> Optional[Session]:
        return session_mgr.get_session(session_id, tenant_id)

    @staticmethod
    def get_or_create_session(session_id: Optional[str], tenant_id: str = "default") -> Session:
        return session_mgr.get_or_create(session_id, tenant_id)

    @staticmethod
    def append_history(session_id: str, message: dict, tenant_id: str = "default"):
        session_mgr.append_history(session_id, message, tenant_id)

    @staticmethod
    def get_history(session_id: str, max_turns: int = RECENT_TURNS_KEEP, tenant_id: str = "default") -> list[dict]:
        session = session_mgr.get_session(session_id, tenant_id)
        if not session:
            return []
        return session.history[-max_turns:]

    @staticmethod
    def get_summary(session_id: str, tenant_id: str = "default") -> str:
        """获取长对话的压缩摘要（存储在 Session 中，随 Redis 持久化）。"""
        session = session_mgr.get_session(session_id, tenant_id)
        if not session:
            return ""
        return getattr(session, "summary", "")

    @staticmethod
    def maybe_compress(session_id: str, tenant_id: str = "default"):
        """当历史轮数超过阈值时，把老消息压缩成摘要并清理。"""
        session = session_mgr.get_session(session_id, tenant_id)
        if not session:
            return

        history = session.history
        total_turns = len(history) // 2  # 一问一答算一轮
        if total_turns <= MAX_HISTORY_TURNS:
            return

        # 保留最近 RECENT_TURNS_KEEP 轮，其余转成摘要
        recent_start = len(history) - RECENT_TURNS_KEEP * 2
        old_messages = history[:recent_start]
        if not old_messages:
            return

        summary_text = _build_summary(old_messages, session.summary)
        session.summary = summary_text
        session.history = history[recent_start:]
        session_mgr._save_to_redis(session, tenant_id)
        logger.info("Session %s 历史已压缩，摘要长度 %d 字符", session_id, len(summary_text))


def _build_summary(old_messages: list[dict], existing_summary: str) -> str:
    """用简单规则生成摘要；有 LLM key 时可升级为 LLM 压缩。"""
    # 目前先做抽取式摘要：把用户明确表达偏好的句子保留下来
    preference_parts = []
    for msg in old_messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        # 保留偏好表达
        prefs = extract_preferences([msg])
        if prefs:
            preference_parts.append(content)

    new_summary = "；".join(preference_parts[:20]) if preference_parts else "用户前期主要进行了商品咨询。"

    if existing_summary:
        return f"{existing_summary}；{new_summary}"
    return new_summary


class SemanticMemory:
    """L3: 语义记忆 — 用户偏好（Redis）+ 商品知识（Qdrant）"""

    @staticmethod
    def extract_preferences(history: list[dict]) -> dict:
        return extract_preferences(history)

    @staticmethod
    def get_preferences(session_id: str, tenant_id: str = "default") -> dict:
        """从 Redis 读取用户偏好（通过 session_mgr 持久化）。"""
        return session_mgr.get_preferences(session_id, tenant_id)

    @staticmethod
    def save_preferences(session_id: str, preferences: dict, tenant_id: str = "default"):
        """将偏好写入 Redis。"""
        if preferences:
            session_mgr.save_preferences(session_id, preferences, tenant_id)


class PerceptualMemory:
    """L4: 感知记忆 — 多模态向量（CLIP + BGE-M3）
    由 nodes.py 中的 embed_image_node / embed_text_node 处理
    """
    pass


def load_memory_to_state(session_id: str, image_id: Optional[str], text: Optional[str], tenant_id: str = "default", frontend_history: list[dict] | None = None) -> dict:
    """从四层记忆加载初始状态。frontend_history 是前端传来的历史消息。"""
    state = WorkingMemory.init_state(session_id, image_id, text)
    state["tenant_id"] = tenant_id

    # 前端传来的历史优先（格式：[{role: "user"|"assistant", text: "..."}]）
    if frontend_history:
        state["history"] = [{"role": m.get("role", ""), "content": m.get("text", "")} for m in frontend_history]
        state["messages"] = [{"role": m["role"], "content": m["content"]} for m in state["history"]]
        state["summary"] = ""
    else:
        # 回退到 Redis 存储的历史
        session = EpisodicMemory.get_or_create_session(session_id, tenant_id)
        state["history"] = session.history[-RECENT_TURNS_KEEP:]
        state["messages"] = [{"role": m["role"], "content": m["content"]} for m in state["history"]]
        state["summary"] = getattr(session, "summary", "")

    # 获取 session 用于偏好和澄清判断
    session = EpisodicMemory.get_or_create_session(session_id, tenant_id)

    # 语义记忆（传入 tenant_id）
    state["preferences"] = SemanticMemory.get_preferences(session_id, tenant_id)

    # 启发式：若上一轮是澄清问题且本轮用户有输入，视为已回答澄清
    if text and session.history:
        last = session.history[-1]
        if last.get("role") == "assistant" and last.get("is_clarify"):
            state["clarify_answered"] = True
            state["need_clarify"] = False

    return state


def save_memory_from_state(state: dict, tenant_id: str = "default"):
    """将 graph 执行结果写回记忆系统"""
    session_id = state.get("session_id", "")
    if not session_id:
        return

    # 情景记忆：保存对话（传入 tenant_id）
    if state.get("final_answer"):
        EpisodicMemory.append_history(session_id, {
            "role": "user",
            "content": state.get("text", ""),
        }, tenant_id)
        # 把候选商品和引用一起存到历史里，前端刷新页面后能重新展示商品卡片
        EpisodicMemory.append_history(session_id, {
            "role": "assistant",
            "content": state["final_answer"],
            "candidates": (state.get("candidates") or [])[:5],  # 只保留 top5，避免 Redis 体积膨胀
            "citations": (state.get("citations") or [])[:5],
        }, tenant_id)
    elif state.get("need_clarify") and state.get("clarify_question"):
        # 澄清分支也存历史（带标记），供下轮恢复上下文
        EpisodicMemory.append_history(session_id, {
            "role": "user",
            "content": state.get("text", ""),
        }, tenant_id)
        EpisodicMemory.append_history(session_id, {
            "role": "assistant",
            "content": state["clarify_question"],
            "is_clarify": True,
        }, tenant_id)

    # 长对话压缩（传入 tenant_id）
    EpisodicMemory.maybe_compress(session_id, tenant_id)

    # 语义记忆：提取偏好并持久化到 Redis（传入 tenant_id）
    session = EpisodicMemory.get_session(session_id, tenant_id)
    if session:
        new_prefs = SemanticMemory.extract_preferences(session.history)
        # 同时把摘要里可能包含的偏好也纳入（简单去重）
        summary_prefs = SemanticMemory.extract_preferences(
            [{"role": "user", "content": getattr(session, "summary", "")}]
        )
        new_prefs.update(summary_prefs)
        if new_prefs:
            SemanticMemory.save_preferences(session_id, new_prefs, tenant_id)
            logger.info("Session %s 提取偏好 %d 条并写入 Redis", session_id, len(new_prefs))
