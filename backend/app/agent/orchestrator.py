import asyncio
import json
import logging

from app.agent.memory import build_messages, extract_preferences
from app.agent.react_loop import react_loop
from app.agent.tools import (
    call_tool,
    get_latest_citations,
)
from app.core.session import session_mgr

logger = logging.getLogger(__name__)


def _format_candidates(candidates: list[dict]) -> str:
    if not candidates:
        return "无"

    lines: list[str] = []
    for candidate in candidates[:5]:
        parts = [f"- {candidate.get('title', 'unknown')}"]
        if candidate.get("price") is not None:
            parts.append(f"价格: {candidate['price']}")
        if candidate.get("category"):
            parts.append(f"分类: {candidate['category']}")
        if candidate.get("description"):
            parts.append(f"描述: {candidate['description']}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _format_citations(citations: list[dict]) -> str:
    if not citations:
        return "无"
    lines = [
        f"- {item.get('sku', '')} [{item.get('source', 'knowledge')}]: {item.get('snippet', '')}"
        for item in citations[:5]
    ]
    return "\n".join(lines)


async def run_flow(
    image_id: str,
    text: str,
    session_id: str,
    queue: asyncio.Queue,
) -> str:
    session = session_mgr.get_session(session_id)
    history = session.history if session else []
    prefs = session.preferences if session else {}

    candidates: list[dict] = []
    if image_id:
        candidates_result = await call_tool("search_by_image", image_id=image_id)
        candidates_data = json.loads(candidates_result)
        candidates = candidates_data.get("candidates", [])

    await queue.put(("candidates", candidates))

    citations: list[dict] = []
    if candidates:
        knowledge_result = await call_tool(
            "search_knowledge",
            sku_ids=[item.get("sku", "") for item in candidates[:3] if item.get("sku")],
            query_text=text or "",
        )
        knowledge_data = json.loads(knowledge_result)
        citations = knowledge_data.get("citations", [])
    else:
        citations = []

    await queue.put(("citations", {"citations": citations}))

    if not candidates and not history:
        fallback = "未找到匹配的商品，请尝试其他图片或补充需求。"
        await queue.put(("delta", {"text": fallback}))
        return fallback

    messages = build_messages(
        history=history,
        text=text,
        candidates_text=_format_candidates(candidates),
        preferences=prefs,
        citations_text=_format_citations(citations),
    )
    session_mgr.append_history(
        session_id,
        {"role": "user", "content": messages[-1]["content"]},
    )

    full_response = await react_loop(messages, queue)

    latest_citations = get_latest_citations()
    if latest_citations and latest_citations != citations:
        await queue.put(("citations", {"citations": latest_citations}))

    if full_response:
        session_mgr.append_history(
            session_id,
            {"role": "assistant", "content": full_response},
        )
        if session:
            session.preferences.update(extract_preferences(session.history))

    return full_response
