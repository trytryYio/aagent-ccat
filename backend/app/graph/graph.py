"""StateGraph 构建与编译"""

import logging

from langgraph.graph import StateGraph, END

from app.graph.state import AgentState
from app.graph.checkpointer import build_checkpointer
from app.graph.nodes import (
    intent_recognition_node,
    plan_node,
    query_rewrite_node,
    slot_filling_node,
    embed_image_node,
    embed_text_node,
    search_node,
    decide_clarify_node,
    ask_clarify_node,
    retrieve_citations_node,
    generate_node,
    reflection_node,
    finalize_node,
    docs_search_node,
    docs_retrieve_node,
)

logger = logging.getLogger(__name__)


def router(state: AgentState) -> str:
    """条件路由：clarify 分支判断"""
    if state.get("need_clarify") and not state.get("clarify_answered"):
        return "clarify"
    return "continue"


def reflection_router(state: AgentState) -> str:
    """反思路由：反思未通过则回到 generate 重试。

    流式模式下重试会导致 generate 二次推送 token，前端回答闪烁。
    演示环境默认通过（reflection_node 仍运行并记录反馈，但不重跑 generate）。
    如需启用重试，需在 chat.py 发 reset_text 事件让前端清空已显示文本。"""
    return "passed"


def build_graph() -> StateGraph:
    """构建并编译 StateGraph"""
    builder = StateGraph(AgentState)

    # === 注册 Nodes ===
    builder.add_node("intent_recognition", intent_recognition_node)
    builder.add_node("plan", plan_node)
    builder.add_node("query_rewrite", query_rewrite_node)
    builder.add_node("slot_filling", slot_filling_node)
    builder.add_node("embed_image", embed_image_node)
    builder.add_node("embed_text", embed_text_node)
    builder.add_node("search", search_node)
    builder.add_node("decide_clarify", decide_clarify_node)
    builder.add_node("ask_clarify", ask_clarify_node)
    builder.add_node("retrieve_citations", retrieve_citations_node)
    builder.add_node("generate", generate_node)
    builder.add_node("reflection", reflection_node)
    builder.add_node("finalize", finalize_node)
    builder.add_node("docs_search", docs_search_node)
    builder.add_node("docs_retrieve", docs_retrieve_node)
    builder.add_node("load_profile", load_profile_node)
    builder.add_node("save_profile", save_profile_node)

    # === 入口 ===
    builder.set_entry_point("load_profile")

    # === 普通边 ===
    builder.add_edge("load_profile", "intent_recognition")
    builder.add_edge("intent_recognition", "plan")

    # === 条件路由：Plan → 根据计划第一步分发 ===
    builder.add_conditional_edges(
        "plan",
        lambda s: s.get("plan", ["ask_clarify"])[0] if s.get("plan") else "ask_clarify",
        {
            "query_rewrite": "query_rewrite",
            "slot_filling": "slot_filling",
            "embed_image": "embed_image",
            "embed_text": "embed_text",
            "ask_clarify": "ask_clarify",
            "docs_search": "docs_search",
        },
    )

    # 检索链路：先改写（可选）再槽位填充再编码再检索
    builder.add_edge("embed_image", "slot_filling")
    # query_rewrite 之后条件分支：文档问答直接走 embed_text，商品推荐走 slot_filling
    builder.add_conditional_edges(
        "query_rewrite",
        lambda s: "embed_text" if s.get("active_chain") == "docs" else "slot_filling",
        {"embed_text": "embed_text", "slot_filling": "slot_filling"},
    )
    builder.add_edge("slot_filling", "embed_text")
    # embed_text 之后条件分支：文档问答走 docs_search，商品推荐走 search
    def _route_after_embed(s: dict) -> str:
        ac = s.get("active_chain")
        logger.info("[Router] after embed_text: active_chain=%s, routing to %s", ac, "docs_search" if ac == "docs" else "search")
        return "docs_search" if ac == "docs" else "search"
    builder.add_conditional_edges(
        "embed_text",
        _route_after_embed,
        {"docs_search": "docs_search", "search": "search"},
    )
    builder.add_edge("search", "decide_clarify")

    # 澄清分支
    builder.add_conditional_edges(
        "decide_clarify",
        router,
        {"clarify": "ask_clarify", "continue": "retrieve_citations"},
    )

    # 文档问答链路
    builder.add_edge("docs_search", "docs_retrieve")
    builder.add_edge("docs_retrieve", "generate")

    # 生成+反思链路
    builder.add_edge("ask_clarify", "finalize")
    builder.add_edge("retrieve_citations", "generate")
    builder.add_edge("generate", "reflection")

    # 反思分支
    builder.add_conditional_edges(
        "reflection",
        reflection_router,
        {"passed": "finalize", "retry": "generate"},
    )

    builder.add_edge("finalize", "save_profile")
    builder.add_edge("save_profile", END)

    # === 编译（带 Redis checkpointer，失败回退内存） ===
    checkpointer = build_checkpointer()
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph StateGraph compiled successfully")
    return graph


# 全局单例
agent_graph = build_graph()