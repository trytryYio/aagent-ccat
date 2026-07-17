"""LangGraph StateGraph 的所有 Node 函数"""

import asyncio
import json
import logging

from app.graph.state import AgentState
from app.graph.tools import (
    embed_image_tool,
    embed_text_tool,
    hybrid_search_tool,
    search_by_image_tool,
    search_by_text_tool,
    retrieve_citations_tool,
)
from app.graph.llm import get_llm
from app.graph.slot_filling import extract_slots

logger = logging.getLogger(__name__)


async def intent_recognition_node(state: AgentState) -> AgentState:
    """意图识别 Node：判断用户想做什么"""
    from rag.prompt_loader import load_prompt

    user_input = f"{'[图片]' if state.get('image_id') else ''} {state.get('text') or ''}"
    prompt = load_prompt("intent_recognition",
                         user_input=user_input,
                         history_count=len(state.get("messages", [])))
    llm = get_llm()
    response = await llm.ainvoke(prompt)
    intent = response.content.strip().lower()
    valid_intents = {"find_similar", "ask_product", "ask_document", "compare", "unclear"}
    if intent not in valid_intents:
        intent = "unclear"
    state["intent"] = intent
    # 提前设置 active_chain 让后续 conditional edge 能正确路由
    if intent == "ask_document":
        state["active_chain"] = "docs"
    state.setdefault("usages", []).append(llm.last_usage.to_dict() if llm.last_usage else {})
    logger.info("[Intent] Recognized: %s", intent)
    return state


async def plan_node(state: AgentState) -> AgentState:
    """Plan & Solve Node：根据输入生成执行计划。"""
    has_image = bool(state.get("image_id"))
    has_text = bool((state.get("text") or "").strip())
    has_history = bool(state.get("history", []))

    steps = []

    # ask_document 意图：走文档问答路径（不需要 slot_filling/embed_image）
    if state.get("intent") == "ask_document":
        if has_text and has_history:
            steps.append("query_rewrite")
        steps.append("embed_text")
        steps.extend(["docs_search", "docs_retrieve", "generate"])
        state["plan"] = steps
        state["plan_step"] = 0
        state["active_chain"] = "docs"
        logger.info("[Plan] intent=ask_document Plan: %s", steps)
        return state

    # 决定第一步
    if has_text and has_history:
        steps.append("query_rewrite")
    elif has_image:
        steps.append("embed_image")
    elif has_text:
        steps.append("slot_filling")
    else:
        state["plan"] = ["ask_clarify"]
        state["plan_step"] = 0
        return state

    if has_text:
        steps.append("slot_filling")
        steps.append("embed_text")

    steps.extend(["search", "retrieve_citations", "generate"])

    state["plan"] = steps
    state["plan_step"] = 0
    logger.info("[Plan] intent=%s Plan: %s", state.get("intent"), state["plan"])
    return state


async def query_rewrite_node(state: AgentState) -> AgentState:
    """Query Rewrite Node：把依赖上下文的 query 改写为独立检索 query。"""
    from rag.prompt_loader import load_prompt

    text = state.get("rewritten_query") or state.get("text", "")
    history = state.get("history", [])
    summary = state.get("summary", "")

    history_text = "\n".join(
        f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')}"
        for m in history[-4:]
    )
    if summary:
        history_text = f"【早期摘要】{summary}\n" + history_text
    if not history_text.strip():
        history_text = "无"

    prompt = load_prompt("query_rewrite", history_text=history_text, text=text)
    llm = get_llm(temperature=0.3)
    response = await llm.ainvoke(prompt)
    rewritten = response.content.strip()
    if not rewritten:
        rewritten = text
    state["rewritten_query"] = rewritten
    state["text"] = rewritten  # 后续所有节点用改写后的 query
    state.setdefault("usages", []).append(llm.last_usage.to_dict() if llm.last_usage else {})
    logger.info("[QueryRewrite] original=%s rewritten=%s", text, rewritten)
    return state


async def slot_filling_node(state: AgentState) -> AgentState:
    """槽位填充 Node：提取预算/品类/偏好等结构化信息。"""
    text = state.get("rewritten_query") or state.get("text", "")
    if not text:
        state["slots"] = {}
        return state

    slots, usage = await extract_slots(text)
    state["slots"] = slots
    if usage:
        state.setdefault("usages", []).append(usage)
    logger.info("[SlotFilling] slots=%s", slots)
    return state


async def embed_image_node(state: AgentState) -> AgentState:
    """图片向量化 Node"""
    image_id = state.get("image_id")
    if not image_id:
        logger.warning("[EmbedImage] No image_id")
        return state
    embedding = await embed_image_tool(image_id)
    state["image_embedding"] = embedding
    logger.info("[EmbedImage] Done, dim=%d", len(embedding) if embedding else 0)
    return state


async def embed_text_node(state: AgentState) -> AgentState:
    """文本向量化 Node: 融合搜索关键词增强检索召回"""
    text = state.get("rewritten_query") or state.get("text", "")
    if not text:
        return state

    # 增强: 将 slots 中的 search_keywords 拼接到文本向量化输入中
    slots = state.get("slots", {})
    search_keywords = slots.get("search_keywords", "")
    if search_keywords:
        enhanced_text = f"{text} {search_keywords}"
        logger.info("[EmbedText] Enhanced text with keywords: %s", search_keywords)
    else:
        enhanced_text = text

    embedding = await embed_text_tool(enhanced_text)
    state["text_embedding"] = embedding
    return state


async def search_node(state: AgentState) -> AgentState:
    """混合检索 Node：QueryRouter 自动选路 → 后处理器管线（过滤+排序+截断）。"""
    from rag.postprocessors import build_default_pipeline
    from rag.router import get_query_router
    from app.graph.slot_filling import get_qdrant_filter

    image_emb = state.get("image_embedding")
    text_emb = state.get("text_embedding")
    query = state.get("rewritten_query") or state.get("text", "") or ""
    slots = state.get("slots", {})
    tenant_id = state.get("tenant_id", "default")

    # 构建 Qdrant 过滤条件（价格/品类/系列硬过滤）
    qdrant_filter = get_qdrant_filter(slots, tenant_id)

    # 通过 QueryRouter 自动选择检索器
    router = get_query_router()
    retriever, retriever_name = router.route(image_emb, text_emb)
    candidates = await retriever.retrieve(
        query=query,
        image_embedding=image_emb,
        text_embedding=text_emb,
        top_k=10,
        filters={"category": slots.get("category"), "qdrant_filter": qdrant_filter},
        tenant_id=tenant_id,
    )

    # 后处理器管线：价格过滤 → 性别过滤 → 相关性优先排序 → 动态 TopK
    pipeline = build_default_pipeline()
    candidates = pipeline.run(candidates, state)

    state["candidates"] = candidates
    logger.info("[Search] %s → %d candidates (slots=%s)",
                retriever_name, len(candidates), slots)
    return state


async def decide_clarify_node(state: AgentState) -> AgentState:
    """置信度判断 Node：使用后处理器管线的 ConfidenceGate。"""
    from rag.postprocessors import build_clarify_gate

    candidates = state.get("candidates", [])
    state["need_clarify"] = False
    state["clarify_question"] = None

    gate = build_clarify_gate()
    gate.process(candidates, state)

    top_candidate = candidates[0] if candidates else {}
    top_metadata = top_candidate.get("metadata") or {}
    top_score = top_metadata.get("confidence_score", top_candidate.get("score", 0))
    logger.info("[Clarify] need=%s, top_score=%.3f, slots=%s",
                state["need_clarify"],
                top_score,
                state.get("slots", {}))
    return state


async def ask_clarify_node(state: AgentState) -> AgentState:
    """生成澄清问题 Node"""
    from rag.prompt_loader import load_prompt

    text = state.get("rewritten_query") or state.get("text", "")
    candidates = state.get("candidates", [])

    prompt = load_prompt("ask_clarify",
                         text=text,
                         candidates_json=json.dumps([c.get("title") for c in candidates[:3]], ensure_ascii=False))
    llm = get_llm(temperature=0.3)
    response = await llm.ainvoke(prompt)
    state["clarify_question"] = response.content.strip()
    state["clarify_answered"] = False
    state["need_clarify"] = True
    state.setdefault("usages", []).append(llm.last_usage.to_dict() if llm.last_usage else {})
    return state


async def retrieve_citations_node(state: AgentState) -> AgentState:
    """引用检索 Node：通过 CitationRetriever 检索知识片段。"""
    from rag.retrievers import CitationRetriever

    candidates = state.get("candidates", [])
    text = state.get("rewritten_query") or state.get("text", "")
    tenant_id = state.get("tenant_id", "default")
    if not candidates:
        state["citations"] = []
        return state

    sku_ids = [c.get("sku", "") for c in candidates[:5] if c.get("sku")]
    text_emb = state.get("text_embedding")

    retriever = CitationRetriever()
    citations = await retriever.retrieve(
        query=text, text_embedding=text_emb,
        filters={"sku_ids": sku_ids}, tenant_id=tenant_id,
    )
    state["citations"] = citations
    logger.info("[Citations] Retrieved %d citations (from %d candidates)", len(citations), len(sku_ids))
    return state


async def generate_node(state: AgentState, config) -> AgentState:
    """LLM 生成回答 Node（流式写入 queue）— 根据 active_chain 选择不同 prompt。"""
    from rag.prompt_loader import load_prompt

    text = state.get("rewritten_query") or state.get("text", "")
    history = state.get("history", [])
    preferences = state.get("preferences", {})
    summary = state.get("summary", "")
    slots = state.get("slots", {})
    active_chain = state.get("active_chain", "product")

    # 历史对话格式化（共用）
    history_parts = []
    if summary:
        history_parts.append(f"【早期对话摘要】\n{summary}")
    for msg in history[-4:]:
        role = "用户" if msg.get("role") == "user" else "助手"
        history_parts.append(f"{role}：{msg.get('content', '')}")
    history_text = "\n".join(history_parts) if history_parts else "无"

    # === 文档问答链路 ===
    if active_chain == "docs":
        chunks = state.get("docs_chunks", [])
        # 构建 chunks_text：包含 parent_title + text + 来源
        chunk_lines = []
        for c in chunks[:5]:
            parent = c.get("parent_title", "")
            text_c = c.get("text", "")
            file_name = c.get("file_name", "")
            page = c.get("page", 0)
            header = f"[{file_name} 第{page}页"
            if parent:
                header += f" {parent}"
            header += "]"
            chunk_lines.append(f"{header}\n{text_c}")
        chunks_text = "\n\n".join(chunk_lines) or "无"

        system_prompt = load_prompt("docs_generate",
                                    chunks_text=chunks_text, text=text)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text or "查询文档"},
        ]
        llm = get_llm(streaming=True).with_config({"tags": ["generate"]})
        full_response = ""
        async for chunk in llm.astream(messages, config=config):
            content = chunk.content or ""
            if content:
                full_response += content
        state["final_answer"] = full_response
        state["generation_done"] = True
        state.setdefault("usages", []).append(llm.last_usage.to_dict() if llm.last_usage else {})
        return state

    # === 商品推荐链路 ===
    candidates = state.get("candidates", [])
    citations = state.get("citations", [])

    cand_text = "\n".join(
        f"- [{c.get('sku', '')}] {c.get('title', '')} 价格:{c.get('price', '')} "
        f"描述:{c.get('description', '')}"
        for c in candidates[:5]
    ) or "无"
    cit_text = "\n".join(
        f"- {c.get('sku', '')}: {c.get('snippet', '')}" for c in citations[:5]
    ) or "无"

    pref_lines = [f"- {k.replace('_preference', '')}: {v}" for k, v in preferences.items()]
    pref_text = "\n".join(pref_lines) if pref_lines else "无"

    from app.graph.slot_filling import format_slots_for_prompt
    slots_text = format_slots_for_prompt(slots)

    system_prompt = load_prompt("generate",
                                cand_text=cand_text, cit_text=cit_text,
                                pref_text=pref_text, slots_text=slots_text,
                                history_text=history_text)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text or "推荐类似的产品"},
    ]

    llm = get_llm(streaming=True).with_config({"tags": ["generate"]})
    full_response = ""
    async for chunk in llm.astream(messages, config=config):
        content = chunk.content or ""
        if content:
            full_response += content
    state["final_answer"] = full_response
    state["generation_done"] = True
    state.setdefault("usages", []).append(llm.last_usage.to_dict() if llm.last_usage else {})
    return state


async def reflection_node(state: AgentState) -> AgentState:
    """Reflection Node: 自我反思修正"""
    from rag.prompt_loader import load_prompt

    answer = state.get("final_answer", "")
    candidates = state.get("candidates", [])
    citations = state.get("citations", [])

    prompt = load_prompt("reflection",
                         candidate_count=len(candidates),
                         citation_count=len(citations),
                         answer_preview=answer[:500])
    llm = get_llm(temperature=0.1)
    response = await llm.ainvoke(prompt)
    feedback = response.content.strip()
    state.setdefault("usages", []).append(llm.last_usage.to_dict() if llm.last_usage else {})

    if feedback.upper().startswith("PASS"):
        state["reflection_passed"] = True
        state["reflection_feedback"] = None
    else:
        state["reflection_passed"] = False
        state["reflection_feedback"] = feedback
        logger.info("[Reflection] Failed: %s", feedback[:100])
    return state


async def finalize_node(state: AgentState) -> AgentState:
    """结束 Node"""
    state["task_complete"] = True
    return state


async def docs_search_node(state: AgentState) -> AgentState:
    """文档检索 Node：DocumentRetriever → 后处理器管线。"""
    from rag.retrievers import DocumentRetriever
    from rag.postprocessors import PostprocessorPipeline, DynamicTopKPostprocessor, ConfidenceGatePostprocessor

    text_emb = state.get("text_embedding")
    query = state.get("rewritten_query") or state.get("text", "") or ""
    if not text_emb:
        state["docs_chunks"] = []
        return state

    retriever = DocumentRetriever()
    chunks = await retriever.retrieve(
        query=query, text_embedding=text_emb, top_k=5,
        tenant_id=state.get("tenant_id", "default"),
    )

    pipeline = PostprocessorPipeline()
    pipeline.add(DynamicTopKPostprocessor(min_keep=2))
    gate = ConfidenceGatePostprocessor(threshold=0.55)
    pipeline.add(gate)

    chunks = pipeline.run(chunks, state)
    state["docs_chunks"] = chunks
    state["active_chain"] = "docs"
    logger.info("[DocsSearch] Found %d chunks", len(chunks))
    return state


async def docs_retrieve_node(state: AgentState) -> AgentState:
    """文档检索 Node：获取完整段落供 LLM 引用。"""
    chunks = state.get("docs_chunks", [])
    state["citations"] = []
    for c in chunks[:5]:
        file_name = c.get("file_name", "")
        page = c.get("page", 0)
        parent = c.get("parent_title", "")
        # 拼出 [来源：<file> 第N页 章节：<title>] 格式
        source_parts = [f"{file_name} 第{page}页"]
        if parent:
            source_parts.append(f"章节：{parent}")
        state["citations"].append({
            "sku": c.get("chunk_id", ""),
            "snippet": c.get("text", ""),
            "source": " ".join(source_parts),
        })
    state["active_chain"] = "docs"
    return state
# === ICM: 跨会话用户画像节点 ===

async def load_profile_node(state: dict) -> dict:
    """加载用户画像节点：在 intent_recognition 之前执行。

    从 Redis 加载跨会话用户画像，注入 state['user_profile']。
    如果 Redis 不可用或无画像，注入空 UserProfile。
    """
    from app.memory.user_profile import UserProfile
    from app.memory.profile_store import profile_store

    user_id = state.get("tenant_id", "default")
    profile = await profile_store.get(user_id)
    if profile is None:
        profile = UserProfile(user_id=user_id)

    state["user_profile"] = profile
    return state


async def save_profile_node(state: dict) -> dict:
    """保存用户画像节点：在 finalize 之后执行。

    从最终 state 中提取交互信息，更新跨会话用户画像到 Redis。
    """
    from app.memory.profile_store import profile_store

    user_id = state.get("tenant_id", "default")
    text = state.get("text") or ""
    profile = state.get("user_profile")

    if profile and text:
        await profile_store.update_from_interaction(
            user_id=user_id,
            text=text,
        )

    return state
