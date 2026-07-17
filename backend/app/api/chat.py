import asyncio
import json
import logging
import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.models import ChatRequest, StopRequest, FinalEvent, UsageInfo
from app.core.session import session_mgr
from app.core.stream_manager import stream_manager
from app.core.tenant_manager import DEFAULT_TENANT_ID
from app.agent.tools import REGISTRY, init_tools

# LangGraph 导入
from app.graph.graph import agent_graph
from app.graph.memory import load_memory_to_state, save_memory_from_state
from app.graph.tools import init_tools as graph_init_tools

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "images")
os.makedirs(UPLOAD_DIR, exist_ok=True)

_SEARCH_RESULTS: dict = {"candidates": []}
init_tools(_SEARCH_RESULTS, UPLOAD_DIR)
graph_init_tools(UPLOAD_DIR)

logger.info(f"Loaded {len(REGISTRY)} tools: {list(REGISTRY.keys())}")


@router.post("/api/v1/chat")
async def create_chat(req: ChatRequest, request: Request):
    if not req.image_id and not req.session_id and not (req.text and req.text.strip()):
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_PARAMS",
            "message": "请提供 image_id、session_id 或 text 至少一项"
        })

    # 从中间件注入的 request.state 获取 tenant_id
    tenant_id = getattr(request.state, "tenant_id", DEFAULT_TENANT_ID)

    session = session_mgr.get_or_create(req.session_id, tenant_id)
    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    stream_url = f"/api/v1/chat/stream?message_id={message_id}"

    task = asyncio.create_task(
        _run_flow(message_id, req.image_id or "", req.text or "", session.session_id, tenant_id, req.history)
    )
    queue = stream_manager.register(message_id, task)

    return {
        "code": 0,
        "message": "success",
        "data": {
            "session_id": session.session_id,
            "message_id": message_id,
            "stream_url": stream_url,
            "tenant_id": tenant_id,  # 返回给前端方便调试
        }
    }


@router.get("/api/v1/chat/history")
async def get_chat_history(session_id: str, request: Request):
    """加载会话历史"""
    tenant_id = getattr(request.state, "tenant_id", DEFAULT_TENANT_ID)
    session = session_mgr.get_session(session_id, tenant_id)
    if not session:
        return {"code": 0, "data": {"history": []}}
    return {
        "code": 0,
        "data": {
            "history": session.history,
            "summary": getattr(session, "summary", ""),
        }
    }


@router.get("/api/v1/chat/stream")
async def stream_chat(message_id: str):
    for attempt in range(50):
        ctx = stream_manager.get(message_id)
        if ctx:
            break
        await asyncio.sleep(0.05)

    if not ctx:
        raise HTTPException(status_code=404, detail={
            "code": "MESSAGE_NOT_FOUND",
            "message": "message_id 无效"
        })

    queue: asyncio.Queue = ctx.queue
    # 断线续传：先把已经 delivery 过的事件重发一遍
    resume_events = list(ctx.delivered_events)

    async def event_generator():
        try:
            # 1. 补发已送达事件（candidates/citations/delta_text 等）
            for etype, edata in resume_events:
                yield f"event: {etype}\ndata: {json.dumps(edata, ensure_ascii=False)}\n\n"

            # 2. 继续从 queue 消费新事件
            while True:
                event_type, data = await asyncio.wait_for(queue.get(), timeout=120)
                if event_type not in ("candidates", "citations", "delta_text", "pipeline_step", "error", "final"):
                    continue
                ctx.delivered_events.append((event_type, data))
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if event_type == "final":
                    ctx.final_payload = data
                    break
        except asyncio.TimeoutError:
            yield f"event: error\ndata: {json.dumps({'message': '生成超时'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # 不要立即 remove：让客户端在 final 后还能再连一次读到 final
            if ctx.final_payload:
                stream_manager.remove(message_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.get("/api/v1/chat/resume")
async def resume_chat(message_id: str):
    """断线重连兜底：若任务已结束/Final 已发出，一次性返回 final 事件。"""
    ctx = stream_manager.get(message_id)
    if ctx and ctx.final_payload:
        return {"code": 0, "finished": True, "data": ctx.final_payload}

    return {"code": 0, "finished": False, "data": None}


@router.post("/api/v1/chat/stop")
async def stop_chat(req: StopRequest):
    stream_manager.cancel(req.message_id)
    return {"code": 0, "message": "success"}


# 节点名称 → 用户友好的中文标签
_NODE_LABELS = {
    "intent_recognition": "识别意图",
    "plan": "制定计划",
    "query_rewrite": "查询改写",
    "slot_filling": "槽位填充",
    "embed_image": "图片编码",
    "embed_text": "文本编码",
    "search": "检索匹配",
    "decide_clarify": "结果评估",
    "ask_clarify": "确认问题",
    "retrieve_citations": "查询知识",
    "generate": "生成答案",
    "reflection": "反思校验",
    "finalize": "完成",
}

# 需要跳过（不展示给用户）的内部节点
_SKIP_NODES = {"finalize"}


async def _run_flow(
    message_id: str,
    image_id: str,
    text: str,
    session_id: str,
    tenant_id: str = "default",
    frontend_history: list[dict] | None = None,
):
    ctx = stream_manager.get(message_id)
    queue = ctx.queue if ctx else asyncio.Queue()

    # 记录已完成的节点，用于追踪进度
    completed_steps: list[str] = []

    try:
        # 1. 从四层记忆系统加载初始状态（传入 tenant_id 和前端历史）
        initial_state = load_memory_to_state(session_id, image_id, text, tenant_id, frontend_history)
        config = {"configurable": {"thread_id": session_id}}

        # 2. 用 astream_events 实现 token 级真流式（边跑边推 SSE）
        _in_generate = False  # track whether we are inside the generate node
        async for event in agent_graph.astream_events(initial_state, config=config, version="v2"):
            kind = event["event"]
            name = event.get("name", "")

            # === 管线进度追踪 ===
            if kind == "on_chain_start" and name in _NODE_LABELS and name not in _SKIP_NODES:
                label = _NODE_LABELS[name]
                # 把前一步标记为完成，当前步标记为进行中
                payload = {
                    "step": name,
                    "label": label,
                    "status": "running",
                    "completed": list(completed_steps),
                }
                await queue.put(("pipeline_step", payload))

            elif kind == "on_chain_end" and name in _NODE_LABELS and name not in _SKIP_NODES:
                completed_steps.append(name)
                label = _NODE_LABELS[name]
                payload = {
                    "step": name,
                    "label": label,
                    "status": "done",
                    "completed": list(completed_steps),
                }
                await queue.put(("pipeline_step", payload))

            elif kind == "on_chain_error" and name in _NODE_LABELS:
                label = _NODE_LABELS[name]
                payload = {
                    "step": name,
                    "label": label,
                    "status": "error",
                    "completed": list(completed_steps),
                }
                await queue.put(("pipeline_step", payload))

            if kind == "on_chain_start" and name == "generate":
                _in_generate = True
            elif kind == "on_chain_end" and name == "generate":
                _in_generate = False
                # generate 节点完成 → 文字推荐已完成，推送商品卡片 + 引用
                final_s = (await agent_graph.aget_state(config)).values
                cands = final_s.get("candidates", [])
                if cands:
                    await queue.put(("candidates", {"candidates": cands}))
                cits = final_s.get("citations", [])
                if cits:
                    await queue.put(("citations", {"citations": cits}))

            # on_chat_model_stream: 流式 LLM token → 逐字推送
            if kind == "on_chat_model_stream" and _in_generate:
                chunk = event["data"].get("chunk")
                token = getattr(chunk, "content", "") or ""
                if token:
                    await queue.put(("delta_text", {"text": token}))

        # 3. 取最终 state 写回记忆 + 处理澄清分支
        final_state = (await agent_graph.aget_state(config)).values
        save_memory_from_state(final_state, tenant_id)

        need_clarify = final_state.get("need_clarify", False)
        # 澄清分支：ask_clarify 用 ainvoke（非流式），其文本需整段补推
        if need_clarify:
            q = final_state.get("clarify_question", "")
            if q:
                await queue.put(("delta_text", {"text": q}))

        # 4. 聚合 token usage 并推送 final
        usages = final_state.get("usages") or []
        total_usage = UsageInfo()
        for u in usages:
            if u:
                total_usage.prompt_tokens += int(u.get("prompt_tokens", 0) or 0)
                total_usage.completion_tokens += int(u.get("completion_tokens", 0) or 0)
                total_usage.total_tokens += int(u.get("total_tokens", 0) or 0)
                if u.get("model"):
                    total_usage.model = u["model"]

        # 5. 记录租户 token 用量（异步，不阻塞响应）
        if total_usage.total_tokens > 0:
            from app.core.tenant_manager import tenant_mgr
            tenant_mgr.incr_token_count(tenant_id, total_usage.total_tokens)
            logger.info("[Usage] tenant=%s tokens=%d", tenant_id, total_usage.total_tokens)

        await queue.put(("final", FinalEvent(
            need_clarify=need_clarify,
            clarify_question=final_state.get("clarify_question"),
            usage=total_usage,
        ).model_dump()))

    except asyncio.CancelledError:
        logger.info(f"Message {message_id} cancelled")
    except Exception as e:
        logger.error(f"Flow error for {message_id}: {e}", exc_info=True)
        await queue.put(("error", {"message": str(e)}))
    finally:
        logger.info(f"Flow completed for {message_id}")
