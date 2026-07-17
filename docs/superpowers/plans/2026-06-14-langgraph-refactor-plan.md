# LangGraph 重构 + GSAP 前端优化 + RAGAS 评估 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Agent 项目重构为 LangGraph StateGraph 多 Agent 编排，引入四层记忆系统，补齐 RAG 数据爬取 + RAGAS 评估 + GSAP 前端动效，最终交付教学 MD 文件 + 毕设大纲。

**Architecture:** 后端用 LangGraph StateGraph 作为多 Agent 编排引擎，包含 Intent Recognition → Plan & Solve → ReAct 循环 → Reflection → 多 Agent 分发；记忆系统分 4 层：工作记忆(AgentState) + 情景记忆(Redis) + 语义记忆(Qdrant) + 感知记忆(CLIP/BGE-M3)；RAG 数据通过 Playwright 爬取李宁官网 + Qdrant 入库 + RAGAS 评估；前端 GSAP 动效不改组件结构。

**Tech Stack:** LangGraph 1.2.1, LangChain, CLIP, BGE-M3, Qdrant, RAGAS, GSAP, Vue 3, FastAPI, Redis, Playwright

**Branch:** `refactor/langgraph-agent` (从 `develop` 切出)

---

## Phase 0: 环境准备与分支

### Task 0.1: 创建分支与目录结构

**Files:**
- Create: `backend/app/graph/__init__.py`
- Create: `backend/app/graph/state.py`
- Create: `backend/app/graph/nodes.py`
- Create: `backend/app/graph/graph.py`
- Create: `backend/app/graph/memory.py`
- Create: `backend/app/graph/tools.py`
- Create: `rag/eval/__init__.py`
- Create: `web/src/composables/useGSAP.ts`
- Create: `docs/teaching/.gitkeep`

- [ ] **Step 1: 创建并切换到新分支**

```bash
git fetch origin develop
git checkout -b refactor/langgraph-agent origin/develop
```

- [ ] **Step 2: 创建新目录结构**

```bash
mkdir -p backend/app/graph
mkdir -p rag/eval
mkdir -p docs/teaching
touch backend/app/graph/__init__.py
touch rag/eval/__init__.py
```

- [ ] **Step 3: 安装 LangGraph 依赖（确认已安装）**

```bash
pip list 2>/dev/null | grep langgraph
# 应有：langgraph 1.2.1, langgraph-checkpoint 4.1.1
```

- [ ] **Step 4: 安装 GSAP 前端依赖**

```bash
cd web && npm install gsap
```

- [ ] **Step 5: 初始提交**

```bash
git add backend/app/graph/ rag/eval/ web/package.json web/package-lock.json
git commit -m "chore: 创建 LangGraph 重构项目结构与安装依赖"
```

---

## Phase 1: LangGraph StateGraph 核心

### Task 1.1: 定义 AgentState

**Files:**
- Create: `backend/app/graph/state.py`

- [ ] **Step 1: 编写 AgentState**

```python
# backend/app/graph/state.py
from typing import TypedDict, Optional, Annotated, Sequence
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """LangGraph 全局状态，继承 MessagesState 的消息历史能力"""

    # === 用户输入 ===
    image_id: Optional[str]
    text: Optional[str]
    session_id: str

    # === 记忆系统 ===
    messages: Annotated[Sequence[dict], add_messages]  # 工作记忆
    preferences: dict  # 语义记忆（用户偏好）
    history: list[dict]  # 情景记忆（会话历史）

    # === 检索相关 ===
    candidates: list[dict]
    image_embedding: Optional[list[float]]
    text_embedding: Optional[list[float]]
    citations: list[dict]

    # === 澄清 ===
    need_clarify: bool
    clarify_question: Optional[str]
    clarify_answered: bool

    # === 多 Agent 编排 ===
    intent: str  # "find_similar" | "ask_product" | "compare" | "unclear"
    plan: list[str]  # Plan & Solve 生成的步骤列表
    plan_step: int  # 当前执行到第几步
    task_complete: bool  # 是否完成

    # === 反思 ===
    reflection_passed: bool
    reflection_feedback: Optional[str]

    # === 流式生成 ===
    generation_done: bool
    final_answer: Optional[str]
```

- [ ] **Step 2: 验证导入**

```python
python -c "from backend.app.graph.state import AgentState; print('OK')"
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/graph/state.py
git commit -m "feat: 定义 LangGraph AgentState 全局状态类型"
```

---

### Task 1.2: 实现 LangGraph Tools（RAG 工具封装）

**Files:**
- Create: `backend/app/graph/tools.py`

```python
# backend/app/graph/tools.py
"""LangGraph 可调用的工具函数，替代旧的 tools.py 中 @register_tool 模式"""

import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_UPLOAD_DIR: str = ""


def init_tools(upload_dir: str):
    global _UPLOAD_DIR
    _UPLOAD_DIR = upload_dir


def _resolve_image_path(image_id: str) -> Optional[str]:
    """解析 image_id 到本地文件路径"""
    exact_jpg = os.path.join(_UPLOAD_DIR, f"{image_id}.jpg")
    if os.path.exists(exact_jpg):
        return exact_jpg
    direct_path = os.path.join(_UPLOAD_DIR, image_id)
    if os.path.exists(direct_path):
        return direct_path
    if os.path.isdir(_UPLOAD_DIR):
        for fname in os.listdir(_UPLOAD_DIR):
            if fname.startswith(image_id):
                return os.path.join(_UPLOAD_DIR, fname)
    return None


async def embed_image_tool(image_id: str) -> list[float]:
    """CLIP 图片向量化"""
    from rag.embedding import embed_image
    image_path = _resolve_image_path(image_id)
    if not image_path:
        raise FileNotFoundError(f"Image not found: {image_id}")
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_image, image_bytes)


async def embed_text_tool(text: str) -> list[float]:
    """BGE-M3 文本向量化"""
    from rag.embedding import embed_text
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_text, text)


async def search_by_image_tool(image_embedding: list[float], top_k: int = 5) -> list[dict]:
    """以图搜图"""
    from rag.image_search import search_by_image
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        None, lambda: search_by_image(image_embedding, top_k=top_k, score_threshold=0.0)
    )
    return [
        {
            "sku": r.product_id,
            "score": float(r.score),
            "title": r.name,
            "image_url": r.image_url or "",
            "price": float(r.price) if r.price else None,
            "description": r.description,
            "category": r.category,
            "need_clarify": bool(r.need_clarify),
        }
        for r in results
    ]


async def hybrid_search_tool(
    image_embedding: Optional[list[float]] = None,
    text_embedding: Optional[list[float]] = None,
    top_k: int = 10,
) -> list[dict]:
    """RRF 混合检索"""
    from rag.hybrid_search import hybrid_search
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        None,
        lambda: hybrid_search(
            image_embedding=image_embedding,
            text_embedding=text_embedding,
            top_k=top_k,
        ),
    )
    return [
        {
            "sku": r.product_id,
            "score": float(r.score),
            "title": r.name,
            "image_url": r.image_url or "",
            "price": float(r.price) if r.price else None,
            "description": r.description,
            "category": r.category,
            "need_clarify": bool(r.need_clarify),
        }
        for r in results
    ]


async def retrieve_citations_tool(sku_ids: list[str], query_text: str = "") -> list[dict]:
    """检索引用知识片段"""
    from rag.embedding import embed_text
    from rag.text_retrieval import get_citations, get_citations_by_sku
    loop = asyncio.get_running_loop()
    citations: list[dict] = []
    if query_text.strip():
        query_emb = await loop.run_in_executor(None, embed_text, query_text.strip())
        if query_emb:
            citations = await loop.run_in_executor(
                None, lambda: get_citations(query_emb, sku_ids, top_k=5)
            )
    if not citations:
        aggregated = []
        for sid in sku_ids[:3]:
            sc = await loop.run_in_executor(None, get_citations_by_sku, sid)
            aggregated.extend(sc)
        citations = aggregated[:5]
    # 标准化
    normalized = []
    for item in citations:
        if not item:
            continue
        sku = str(item.get("product_id") or item.get("sku") or "")
        normalized.append({
            "sku": sku,
            "id": f"{sku}:{item.get('tag', 'knowledge')}",
            "snippet": item.get("content") or item.get("snippet") or "",
            "source": item.get("tag", "knowledge"),
        })
    return normalized
```

- [ ] **Step 1: 编写 tools.py**

```bash
# 写入上述内容到 backend/app/graph/tools.py
```

- [ ] **Step 2: 验证导入**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python -c "from backend.app.graph import tools; print('Tools OK')"
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/graph/tools.py
git commit -m "feat: 实现 LangGraph 工具函数封装"
```

---

### Task 1.3: 实现 Graph Nodes

**Files:**
- Create: `backend/app/graph/nodes.py`

```python
# backend/app/graph/nodes.py
"""LangGraph StateGraph 的所有 Node 函数"""

import asyncio
import json
import logging
from typing import Literal

from backend.app.graph.state import AgentState
from backend.app.graph.tools import (
    embed_image_tool,
    embed_text_tool,
    hybrid_search_tool,
    search_by_image_tool,
    retrieve_citations_tool,
)
from backend.app.graph.llm import get_llm
from app.core.session import session_mgr

logger = logging.getLogger(__name__)


async def intent_recognition_node(state: AgentState) -> AgentState:
    """意图识别：判断用户想做什么"""
    prompt = f"""
    根据用户输入判断意图（只返回一个词）：
    - find_similar: 用户上传了图片，想找同款或相似商品
    - ask_product: 用户询问商品详情、价格、评价等
    - compare: 用户要求对比多个商品
    - unclear: 无法确定

    用户输入：{"[图片]" if state.get("image_id") else ""} {state.get("text") or ""}
    会话历史条数：{len(state.get("messages", []))}
    """
    from backend.app.graph.llm import get_llm
    llm = get_llm()
    intent = await llm.ainvoke(prompt)
    intent = intent.content.strip().lower()
    valid_intents = {"find_similar", "ask_product", "compare", "unclear"}
    if intent not in valid_intents:
        intent = "unclear"
    state["intent"] = intent
    logger.info(f"[Intent] Recognized: {intent}")
    return state


async def plan_node(state: AgentState) -> AgentState:
    """Plan & Solve：根据意图生成执行计划"""
    intent = state.get("intent", "unclear")
    plans = {
        "find_similar": ["embed_image", "search", "retrieve_citations", "generate"],
        "ask_product": ["embed_image", "search", "retrieve_citations", "generate"],
        "compare": ["embed_image", "search", "retrieve_citations", "generate"],
        "unclear": ["ask_clarify"],
    }
    state["plan"] = plans.get(intent, ["ask_clarify"])
    state["plan_step"] = 0
    logger.info(f"[Plan] Plan: {state['plan']}")
    return state


async def embed_image_node(state: AgentState) -> AgentState:
    """图片向量化 Node"""
    image_id = state.get("image_id")
    if not image_id:
        logger.warning("[EmbedImage] No image_id")
        return state
    embedding = await embed_image_tool(image_id)
    state["image_embedding"] = embedding
    logger.info(f"[EmbedImage] Done, dim={len(embedding) if embedding else 0}")
    return state


async def embed_text_node(state: AgentState) -> AgentState:
    """文本向量化 Node"""
    text = state.get("text", "")
    if not text:
        return state
    embedding = await embed_text_tool(text)
    state["text_embedding"] = embedding
    return state


async def search_node(state: AgentState) -> AgentState:
    """混合检索 Node"""
    image_emb = state.get("image_embedding")
    text_emb = state.get("text_embedding")

    if image_emb and text_emb:
        candidates = await hybrid_search_tool(
            image_embedding=image_emb, text_embedding=text_emb, top_k=10
        )
    elif image_emb:
        candidates = await search_by_image_tool(image_emb, top_k=5)
    else:
        candidates = []
    state["candidates"] = candidates
    logger.info(f"[Search] Found {len(candidates)} candidates")
    return state


async def decide_clarify_node(state: AgentState) -> AgentState:
    """置信度判断 Node"""
    candidates = state.get("candidates", [])
    state["need_clarify"] = False
    state["clarify_question"] = None

    if not candidates:
        state["need_clarify"] = True
        state["clarify_question"] = "暂时没有找到匹配的商品，能描述一下你想要的商品类型或者预算范围吗？"
        return state

    top_score = candidates[0].get("score", 0) if candidates else 0
    if top_score < 0.6:
        state["need_clarify"] = True
        state["clarify_question"] = "搜索结果匹配度不高，请问你的预算大概在什么范围？或者有偏好的品牌吗？"
    elif len(candidates) >= 2:
        gap = candidates[0].get("score", 0) - candidates[1].get("score", 0)
        if gap < 0.05:
            state["need_clarify"] = True
            state["clarify_question"] = "有几款商品比较接近，你更看重性价比还是性能？"

    logger.info(f"[Clarify] need={state['need_clarify']}, top_score={top_score:.3f}")
    return state


async def ask_clarify_node(state: AgentState) -> AgentState:
    """生成澄清问题 Node"""
    text = state.get("text", "")
    candidates = state.get("candidates", [])

    prompt = f"""
    用户需求：{text}
    候选商品：{json.dumps([c.get("title") for c in candidates[:3]], ensure_ascii=False)}
    请生成一个简短的澄清问题，帮助进一步缩小推荐范围（预算/用途/偏好）。
    只输出问题本身。
    """
    from backend.app.graph.llm import get_llm
    llm = get_llm(temperature=0.3)
    response = await llm.ainvoke(prompt)
    state["clarify_question"] = response.content.strip()
    state["clarify_answered"] = False
    return state


async def retrieve_citations_node(state: AgentState) -> AgentState:
    """引用检索 Node"""
    candidates = state.get("candidates", [])
    text = state.get("text", "")
    if not candidates:
        state["citations"] = []
        return state

    sku_ids = [c.get("sku", "") for c in candidates[:3] if c.get("sku")]
    citations = await retrieve_citations_tool(sku_ids, text)
    state["citations"] = citations
    logger.info(f"[Citations] Retrieved {len(citations)} citations")
    return state


async def generate_node(state: AgentState) -> AgentState:
    """LLM 生成回答 Node（流式写入 queue）"""
    candidates = state.get("candidates", [])
    citations = state.get("citations", [])
    text = state.get("text", "")
    history = state.get("history", [])
    preferences = state.get("preferences", {})

    # 构建 system prompt
    cand_text = "\n".join(
        f"- {c.get('title', '')} 价格:{c.get('price', '')} 分类:{c.get('category', '')}"
        for c in candidates[:5]
    ) or "无"
    cit_text = "\n".join(
        f"- {c.get('sku', '')}: {c.get('snippet', '')}" for c in citations[:5]
    ) or "无"

    system_prompt = f"""你是电商导购助手。
你必须依据候选商品和知识引用回答，不要编造未提供的商品信息。
如果信息不足，请说明不确定。

候选商品：
{cand_text}

知识引用：
{cit_text}

用户历史偏好：
{json.dumps(preferences, ensure_ascii=False)}

输出要求：
- 使用中文回答
- 推荐理由要引用候选商品属性或知识引用
- 不要推荐候选列表之外的商品"""

    messages = [{"role": "system", "content": system_prompt}]
    # 添加历史
    for msg in history[-4:]:
        messages.append(msg)
    # 添加当前
    messages.append({"role": "user", "content": text or "推荐类似的产品"})

    from backend.app.graph.llm import get_llm
    llm = get_llm(streaming=True)
    full_response = ""
    async for chunk in llm.astream(messages):
        content = chunk.content or ""
        if content:
            full_response += content
    state["final_answer"] = full_response
    state["generation_done"] = True
    return state


async def reflection_node(state: AgentState) -> AgentState:
    """Reflection：自我反思修正"""
    answer = state.get("final_answer", "")
    candidates = state.get("candidates", [])
    citations = state.get("citations", [])

    prompt = f"""
    检查以下回答是否符合要求：
    1. 是否基于候选商品？候选有 {len(candidates)} 个
    2. 是否引用了知识？引用有 {len(citations)} 条
    3. 是否有编造的内容？
    4. 是否直接回答了用户问题？

    回答：{answer[:500]}

    如果合格只输出 PASS，否则说明具体问题。
    """
    from backend.app.graph.llm import get_llm
    llm = get_llm(temperature=0.1)
    response = await llm.ainvoke(prompt)
    feedback = response.content.strip()

    if feedback.upper().startswith("PASS"):
        state["reflection_passed"] = True
        state["reflection_feedback"] = None
    else:
        state["reflection_passed"] = False
        state["reflection_feedback"] = feedback
        logger.info(f"[Reflection] Failed: {feedback[:100]}")
    return state


async def finalize_node(state: AgentState) -> AgentState:
    """结束 Node"""
    state["task_complete"] = True
    return state
```

- [ ] **Step 1: 编写 nodes.py**

- [ ] **Step 2: 验证导入**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python -c "from backend.app.graph import nodes; print('Nodes OK')"
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/graph/nodes.py
git commit -m "feat: 实现 LangGraph 所有 Node 函数"
```

---

### Task 1.4: LLM 工厂模块（解决循环导入）

**Files:**
- Create: `backend/app/graph/llm.py`

```python
# backend/app/graph/llm.py
"""LLM 工厂，被 nodes.py 和 graph.py 共同引用"""

from langchain_openai import ChatOpenAI
from app.config import settings

_llm = None


def get_llm(temperature: float = None, streaming: bool = False) -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=temperature or settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout,
            streaming=streaming,
        )
    return _llm
```

- [ ] **Step 1: 创建 llm.py**

```bash
touch backend/app/graph/llm.py && # 写入上述内容
```

- [ ] **Step 2: 验证导入**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python -c "from backend.app.graph.llm import get_llm; print('LLM OK')"
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/graph/llm.py
git commit -m "feat: 添加 LLM 工厂模块"
```

**Files:**
- Create: `backend/app/graph/graph.py`

```python
# backend/app/graph/graph.py
"""StateGraph 构建与编译"""

import logging
from typing import Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI

from backend.app.graph.state import AgentState
from backend.app.graph.nodes import (
    intent_recognition_node,
    plan_node,
    embed_image_node,
    embed_text_node,
    search_node,
    decide_clarify_node,
    ask_clarify_node,
    retrieve_citations_node,
    generate_node,
    reflection_node,
    finalize_node,
)

logger = logging.getLogger(__name__)


def router(state: AgentState) -> str:
    """条件路由：clarify 分支判断"""
    if state.get("need_clarify") and not state.get("clarify_answered"):
        return "clarify"
    return "continue"


def reflection_router(state: AgentState) -> str:
    """反思路由：如果反思未通过，回到 generate"""
    if state.get("reflection_passed"):
        return "passed"
    return "retry"


def build_graph() -> StateGraph:
    """构建并编译 StateGraph"""
    builder = StateGraph(AgentState)

    # === 注册 Nodes ===
    builder.add_node("intent_recognition", intent_recognition_node)
    builder.add_node("plan", plan_node)
    builder.add_node("embed_image", embed_image_node)
    builder.add_node("embed_text", embed_text_node)
    builder.add_node("search", search_node)
    builder.add_node("decide_clarify", decide_clarify_node)
    builder.add_node("ask_clarify", ask_clarify_node)
    builder.add_node("retrieve_citations", retrieve_citations_node)
    builder.add_node("generate", generate_node)
    builder.add_node("reflection", reflection_node)
    builder.add_node("finalize", finalize_node)

    # === 入口 ===
    builder.set_entry_point("intent_recognition")

    # === 普通边 ===
    builder.add_edge("intent_recognition", "plan")

    # === 条件路由：Plan → 根据意图分发 ===
    builder.add_conditional_edges(
        "plan",
        lambda s: s.get("plan", ["ask_clarify"])[0] if s.get("plan") else "ask_clarify",
        {
            "embed_image": "embed_image",
            "ask_clarify": "ask_clarify",
        },
    )

    # 检索链路
    builder.add_edge("embed_image", "embed_text")
    builder.add_edge("embed_text", "search")
    builder.add_edge("search", "decide_clarify")

    # 澄清分支
    builder.add_conditional_edges(
        "decide_clarify",
        router,
        {"clarify": "ask_clarify", "continue": "retrieve_citations"},
    )

    # 生成+反思链路
    builder.add_edge("ask_clarify", "finalize")  # 澄清后直接结束（等待用户输入）
    builder.add_edge("retrieve_citations", "generate")
    builder.add_edge("generate", "reflection")

    # 反思分支
    builder.add_conditional_edges(
        "reflection",
        reflection_router,
        {"passed": "finalize", "retry": "generate"},
    )

    builder.add_edge("finalize", END)

    # === 编译（带 MemorySaver checkpointer） ===
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)
    logger.info("LangGraph StateGraph compiled successfully")
    return graph


# 全局单例
agent_graph = build_graph()
```

- [ ] **Step 1: 编写 graph.py**

- [ ] **Step 2: 验证编译**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python -c "
from backend.app.graph.graph import agent_graph
print('Graph compiled OK')
print(agent_graph.get_graph().draw_mermaid())
"
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/graph/graph.py
git commit -m "feat: 构建并编译 LangGraph StateGraph"
```

---

### Task 1.5: 实现四层记忆系统

**Files:**
- Create: `backend/app/graph/memory.py`

```python
# backend/app/graph/memory.py
"""四层记忆系统：工作记忆 + 情景记忆 + 语义记忆 + 感知记忆"""

import json
import logging
from typing import Optional

from app.core.session import Session, session_mgr
from app.agent.memory import extract_preferences

logger = logging.getLogger(__name__)


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
            "preferences": {},
            "history": [],
            "messages": [],
        }


class EpisodicMemory:
    """L2: 情景记忆 — 会话历史（Redis）"""

    @staticmethod
    def get_session(session_id: str) -> Optional[Session]:
        return session_mgr.get_session(session_id)

    @staticmethod
    def get_or_create_session(session_id: Optional[str]) -> Session:
        return session_mgr.get_or_create(session_id)

    @staticmethod
    def append_history(session_id: str, message: dict):
        session_mgr.append_history(session_id, message)

    @staticmethod
    def get_history(session_id: str, max_turns: int = 6) -> list[dict]:
        session = session_mgr.get_session(session_id)
        if not session:
            return []
        return session.history[-max_turns:]


class SemanticMemory:
    """L3: 语义记忆 — 用户偏好 + 商品知识（Qdrant）"""

    @staticmethod
    def extract_preferences(history: list[dict]) -> dict:
        return extract_preferences(history)

    @staticmethod
    def get_preferences(session_id: str) -> dict:
        session = session_mgr.get_session(session_id)
        return session.preferences if session else {}


class PerceptualMemory:
    """L4: 感知记忆 — 多模态向量（CLIP + BGE-M3）
    由 nodes.py 中的 embed_image_node / embed_text_node 处理
    """
    pass


def load_memory_to_state(session_id: str, image_id: Optional[str], text: Optional[str]) -> dict:
    """从四层记忆加载初始状态"""
    state = WorkingMemory.init_state(session_id, image_id, text)

    # 情景记忆
    session = EpisodicMemory.get_or_create_session(session_id)
    state["history"] = session.history[-6:]
    state["messages"] = [{"role": m["role"], "content": m["content"]} for m in state["history"]]

    # 语义记忆
    state["preferences"] = SemanticMemory.get_preferences(session_id)

    return state


def save_memory_from_state(state: dict):
    """将 graph 执行结果写回记忆系统"""
    session_id = state.get("session_id", "")
    if not session_id:
        return

    # 情景记忆：保存对话
    if state.get("final_answer"):
        EpisodicMemory.append_history(session_id, {
            "role": "user",
            "content": state.get("text", ""),
        })
        EpisodicMemory.append_history(session_id, {
            "role": "assistant",
            "content": state["final_answer"],
        })

    # 语义记忆：提取偏好
    session = EpisodicMemory.get_session(session_id)
    if session:
        new_prefs = SemanticMemory.extract_preferences(session.history)
        session.preferences.update(new_prefs)
```

- [ ] **Step 1: 编写 memory.py**

- [ ] **Step 2: 验证导入**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python -c "from backend.app.graph.memory import WorkingMemory, EpisodicMemory; print('Memory OK')"
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/graph/memory.py
git commit -m "feat: 实现四层记忆系统"
```

---

## Phase 2: 后端 API 集成

### Task 2.1: 更新 chat.py 使用新 Graph

**Files:**
- Modify: `backend/app/api/chat.py`

- [ ] **Step 1: 修改 create_chat 接口，改为调用 LangGraph**

在 `backend/app/api/chat.py` 中修改 `_run_flow` 函数：

```python
# 在文件顶部增加导入
from backend.app.graph.graph import agent_graph
from backend.app.graph.memory import load_memory_to_state, save_memory_from_state

# 将原有的 _run_flow 函数替换为：
async def _run_flow(
    message_id: str,
    image_id: str,
    text: str,
    session_id: str,
):
    ctx = stream_manager.get(message_id)
    queue = ctx.queue if ctx else asyncio.Queue()

    try:
        # 1. 从记忆系统加载初始状态
        initial_state = load_memory_to_state(session_id, image_id, text)

        # 2. 执行 LangGraph（使用 thread_id = session_id 隔离）
        final_state = await agent_graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": session_id}},
        )

        # 3. 将结果写回记忆系统
        save_memory_from_state(final_state)

        # 4. 发送 SSE 事件
        candidates = final_state.get("candidates", [])
        if candidates:
            await queue.put(("candidates", candidates))

        citations = final_state.get("citations", [])
        if citations:
            await queue.put(("citations", {"citations": citations}))

        need_clarify = final_state.get("need_clarify", False)
        if need_clarify:
            q = final_state.get("clarify_question", "")
            await queue.put(("delta", {"text": q}))
        else:
            answer = final_state.get("final_answer", "")
            if answer:
                for i in range(0, len(answer), 20):
                    await queue.put(("delta", {"text": answer[i:i+20]}))
                    await asyncio.sleep(0.02)

        await queue.put(("final", {"need_clarify": need_clarify, "clarify_question": final_state.get("clarify_question")}))

    except asyncio.CancelledError:
        logger.info(f"Message {message_id} cancelled")
    except Exception as e:
        logger.error(f"Flow error for {message_id}: {e}", exc_info=True)
        await queue.put(("error", {"message": str(e)}))
    finally:
        logger.info(f"Flow completed for {message_id}")
```

- [ ] **Step 2: 提交**

```bash
git add backend/app/api/chat.py
git commit -m "feat: 集成 LangGraph 到 API 层"
```

---

### Task 2.2: 数据流验证（启动后端测试）

- [ ] **Step 1: 启动后端服务**

```bash
cd /home/user/projects/AgentProject/Agent
source backend/.venv/bin/activate
PYTHONPATH=$(pwd) python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: 验证健康检查**

```bash
curl http://localhost:8000/api/v1/health
# 预期返回：{"status": "ok", ...}
```

- [ ] **Step 3: 验证 Graph 加载日志**

检查后端启动日志中是否有 `LangGraph StateGraph compiled successfully` 输出。

---

## Phase 3: RAG 数据爬取与入库

### Task 3.1: 修复爬虫脚本

**Files:**
- Modify: `rag/scripts/lining_scraper.py` → 修复路径硬编码

- [ ] **Step 1: 替换硬编码路径为动态检测**

```bash
# 将 lining_scraper.py 中的：
# BASE_DIR = r"d:\Trae CN Work\Rag-Agent"
# 替换为：
# BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

```python
# 修改后的路径部分
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "rag", "data")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
PRODUCTS_CSV = os.path.join(DATA_DIR, "products.csv")
```

- [ ] **Step 2: 运行爬虫验证**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python rag/scripts/lining_scraper.py
```

- [ ] **Step 3: 提交换名**

```bash
cp rag/scripts/lining_scraper.py rag/scripts/lining_scraper_v2.py
git add rag/scripts/lining_scraper_v2.py
git commit -m "fix: 修复爬虫路径硬编码，迁移至 v2"
```

---

### Task 3.2: 数据入库脚本

**Files:**
- Create: `rag/scripts/seed_data.py`

```python
"""种子数据入库：读取 products.csv → 向量化 → 写入 Qdrant → 生成 citations"""

import os
import sys
import csv
import logging

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from rag.embedding import embed_image, embed_text
from rag.db_client import get_qdrant_client
from qdrant_client.models import PointStruct
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CSV_PATH = os.path.join(BASE_DIR, "rag", "data", "products.csv")
IMAGES_DIR = os.path.join(BASE_DIR, "rag", "data", "images")
COLLECTION = "products"


def load_products():
    products = []
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            products.append(row)
    return products


def seed():
    products = load_products()
    logger.info(f"Loaded {len(products)} products")

    client = get_qdrant_client()
    points = []

    for i, p in enumerate(products):
        pid = p.get("product_id", f"product_{i:03d}")
        name = p.get("name", "")
        desc = p.get("description", "")
        price = float(p.get("price", 0))
        category = p.get("category", "")
        image_url = p.get("image_url", "")

        # 文本向量
        text_content = f"{name} {desc} {category}"
        text_emb = embed_text(text_content)

        # 图片向量（如果有本地图片）
        image_emb = None
        img_path = os.path.join(IMAGES_DIR, f"{pid}.jpg")
        if os.path.exists(img_path):
            with open(img_path, "rb") as f:
                image_emb = embed_image(f.read())

        point_id = uuid.uuid5(uuid.NAMESPACE_DNS, pid)
        points.append(PointStruct(
            id=str(point_id),
            vector={
                "text": text_emb,
                "image": image_emb if image_emb else text_emb[:512],  # fallback
            } if image_emb else {
                "text": text_emb,
            },
            payload={
                "product_id": pid,
                "name": name,
                "price": price,
                "description": desc,
                "category": category,
                "image_url": image_url,
            },
        ))

        if (i + 1) % 20 == 0:
            client.upsert(collection_name=COLLECTION, points=points)
            logger.info(f"Upserted {i + 1}/{len(products)}")
            points = []

    if points:
        client.upsert(collection_name=COLLECTION, points=points)

    logger.info(f"Seed complete: {len(products)} products inserted")

    # 自动调用 citations 精细化
    from rag.scripts.refine_knowledge_base import refine
    refine()
    logger.info("Citations refined")


if __name__ == "__main__":
    seed()
```

- [ ] **Step 1: 编写 seed_data.py**

- [ ] **Step 2: 运行入库（需要先有 products.csv 和数据）**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python rag/scripts/seed_data.py
```

- [ ] **Step 3: 提交**

```bash
git add rag/scripts/seed_data.py
git commit -m "feat: 实现种子数据入库脚本"
```

---

## Phase 4: RAGAS 评估流水线

### Task 4.1: RAGAS 评估配置

**Files:**
- Create: `rag/eval/config.py`

```python
# rag/eval/config.py
"""RAGAS 评估配置"""

from pydantic_settings import BaseSettings


class RagasSettings(BaseSettings):
    """RAGAS 评估设置"""
    # 评测 LLM（用于 LLM-as-Judge）
    eval_llm_api_key: str = ""
    eval_llm_base_url: str = "https://api.deepseek.com/v1"
    eval_llm_model: str = "deepseek-chat"

    # 数据集路径
    eval_dataset: str = "rag/eval/datasets/rag_eval_dataset.jsonl"
    e2e_dataset: str = "rag/eval/datasets/rag_e2e_dataset.jsonl"

    # 报告输出
    output_dir: str = "rag/eval/reports"

    model_config = {"env_file": "backend/.env", "extra": "ignore"}


ragas_settings = RagasSettings()
```

- [ ] **Step 1: 编写 config.py**

- [ ] **Step 2: 提交**

```bash
git add rag/eval/config.py
git commit -m "feat: 添加 RAGAS 评估配置"
```

---

### Task 4.2: RAGAS 评估流水线核心

**Files:**
- Create: `rag/eval/ragas_eval.py`

```python
"""RAGAS 评估流水线：四维指标计算"""

import json
import os
import sys
import logging
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_recall, context_precision
from ragas.llms import LangchainLLMWrapper
from langchain_openai import ChatOpenAI

from rag.eval.config import ragas_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_dataset(path: str) -> list[dict]:
    """加载 JSONL 评测数据集"""
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def convert_to_ragas_format(cases: list[dict]) -> dict:
    """将评测用例转换为 RAGAS 需要的格式"""
    questions = []
    answers = []
    contexts = []
    ground_truths = []

    for c in cases:
        questions.append(c.get("query_text", ""))
        answers.append(c.get("answer", ""))
        contexts.append(c.get("retrieved_contexts", []))
        ground_truths.append(c.get("gold_sku_id", ""))

    return {
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truths,
    }


def run_ragas_eval(dataset_path: str, output_dir: str) -> dict:
    """执行 RAGAS 评估"""
    # 1. 加载数据集
    cases = load_dataset(dataset_path)
    logger.info(f"Loaded {len(cases)} evaluation cases")

    # 2. 转换格式
    data = convert_to_ragas_format(cases)
    dataset = Dataset.from_dict(data)

    # 3. 配置 LLM-as-Judge
    eval_llm = ChatOpenAI(
        model=ragas_settings.eval_llm_model,
        api_key=ragas_settings.eval_llm_api_key,
        base_url=ragas_settings.eval_llm_base_url,
        temperature=0.1,
    )
    # 包装为 LangchainLLMWrapper（RAGAS LLM-as-Judge）
    from ragas.llms import LangchainLLMWrapper
    evaluator_llm = LangchainLLMWrapper(eval_llm)

    # 4. 计算指标
    metrics = [faithfulness, answer_relevancy, context_recall, context_precision]
    result = evaluate(dataset, metrics=metrics, llm=evaluator_llm)

    # 5. 输出报告
    os.makedirs(output_dir, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "run_id": run_id,
        "dataset": dataset_path,
        "metrics": {
            "faithfulness": float(result["faithfulness"]),
            "answer_relevancy": float(result["answer_relevancy"]),
            "context_recall": float(result["context_recall"]),
            "context_precision": float(result["context_precision"]),
        },
        "num_cases": len(cases),
    }

    report_path = os.path.join(output_dir, f"ragas_report_{run_id}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 可读报告
    md_lines = [
        f"# RAGAS 评估报告 ({run_id})",
        f"",
        f"| 指标 | 得分 | 说明 |",
        f"|------|------|------|",
        f"| **Faithfulness** | {report['metrics']['faithfulness']:.3f} | 答案忠于检索内容的程度 |",
        f"| **Answer Relevancy** | {report['metrics']['answer_relevancy']:.3f} | 答案相关性 |",
        f"| **Context Recall** | {report['metrics']['context_recall']:.3f} | 检索覆盖率 |",
        f"| **Context Precision** | {report['metrics']['context_precision']:.3f} | 排序质量 |",
        f"",
        f"测试用例数: {report['num_cases']}",
    ]
    md_path = os.path.join(output_dir, f"ragas_report_{run_id}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    logger.info(f"Report saved to {report_path}")
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=ragas_settings.eval_dataset)
    parser.add_argument("--output-dir", default=ragas_settings.output_dir)
    args = parser.parse_args()
    run_ragas_eval(args.dataset, args.output_dir)
```

- [ ] **Step 1: 编写 ragas_eval.py**

- [ ] **Step 2: 安装 RAGAS**

```bash
pip install ragas datasets
```

- [ ] **Step 3: 验证 RAGAS 导入**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python -c "
from ragas.metrics import faithfulness, answer_relevancy
from datasets import Dataset
print('RAGAS OK')
"
```

- [ ] **Step 4: 提交**

```bash
git add rag/eval/
git commit -m "feat: 实现 RAGAS 评估流水线"
```

---

## Phase 5: GSAP 前端优化

### Task 5.1: GSAP 组合函数

**Files:**
- Create: `web/src/composables/useGSAP.ts`

```typescript
// web/src/composables/useGSAP.ts
import { gsap } from 'gsap'
import { onMounted, ref, type Ref } from 'vue'

/**
 * 消息入场动画：淡入 + 上滑
 */
export function useMessageAnimation() {
  const animateIn = (el: HTMLElement, delay: number = 0) => {
    gsap.fromTo(
      el,
      { opacity: 0, y: 20 },
      { opacity: 1, y: 0, duration: 0.4, delay, ease: 'power2.out' }
    )
  }
  return { animateIn }
}

/**
 * 商品卡片交错入场
 */
export function useCardStagger() {
  const animateCards = (els: HTMLElement[]) => {
    gsap.fromTo(
      els,
      { opacity: 0, scale: 0.8, y: 30 },
      {
        opacity: 1,
        scale: 1,
        y: 0,
        duration: 0.35,
        stagger: 0.08,
        ease: 'back.out(1.5)',
      }
    )
  }
  return { animateCards }
}

/**
 * 骨架屏 shimmer 动效
 */
export function useShimmerAnimation(elRef: Ref<HTMLElement | null>) {
  const startShimmer = () => {
    if (!elRef.value) return
    gsap.to(elRef.value, {
      backgroundPosition: '200% 0',
      duration: 1.5,
      repeat: -1,
      ease: 'none',
    })
  }
  const stopShimmer = () => {
    if (!elRef.value) return
    gsap.killTweensOf(elRef.value)
  }
  return { startShimmer, stopShimmer }
}

/**
 * 弹窗动效
 */
export function useDialogAnimation() {
  const animateIn = (el: HTMLElement) => {
    gsap.fromTo(
      el,
      { opacity: 0, scale: 0.9, backdropFilter: 'blur(0px)' },
      { opacity: 1, scale: 1, duration: 0.3, ease: 'power2.out' }
    )
  }
  const animateOut = (el: HTMLElement) => {
    gsap.to(el, {
      opacity: 0,
      scale: 0.9,
      duration: 0.2,
      ease: 'power2.in',
    })
  }
  return { animateIn, animateOut }
}

/**
 * 上传进度条动画
 */
export function useProgressAnimation() {
  const animateProgress = (el: HTMLElement, from: number, to: number) => {
    gsap.fromTo(
      el,
      { width: `${from}%` },
      { width: `${to}%`, duration: 0.5, ease: 'power2.out' }
    )
  }
  return { animateProgress }
}
```

- [ ] **Step 1: 安装 gsap**

```bash
cd web && npm install gsap
```

- [ ] **Step 2: 编写 useGSAP.ts**

- [ ] **Step 3: 提交**

```bash
git add web/src/composables/useGSAP.ts web/package.json web/package-lock.json
git commit -m "feat: 实现 GSAP 动画组合函数"
```

---

### Task 5.2: 在组件中接入 GSAP 动画

**Files:**
- Modify: `web/src/components/MessageBubble.vue`
- Modify: `web/src/components/CandidateCard.vue`
- Modify: `web/src/components/SkeletonCard.vue`
- Modify: `web/src/components/MessageList.vue`
- Modify: `web/src/components/ImageUploadDialog.vue`

- [ ] **Step 1: MessageBubble 接入入场动画**

在 `MessageBubble.vue` 的 `onMounted` 中添加：

```typescript
import { onMounted, ref } from 'vue'
import { useMessageAnimation } from '../composables/useGSAP'

const bubbleRef = ref<HTMLElement | null>(null)
const { animateIn } = useMessageAnimation()

onMounted(() => {
  if (bubbleRef.value) {
    animateIn(bubbleRef.value)
  }
})
```

- [ ] **Step 2: CandidateCard 接入交错动画**

在 `CandidateCard.vue` 的 `onMounted` 中添加卡片入场动效。

- [ ] **Step 3: SkeletonCard 接入 shimmer**

在 `SkeletonCard.vue` 的 `onMounted` 中启动 shimmer 动画。

- [ ] **Step 4: 提交**

```bash
git add web/src/components/
git commit -m "feat: 接入 GSAP 动画到各组件"
```

---

## Phase 6: 教学文件

### Task 6.1: 编写教学 MD 文件

**Files:**
- Create: `docs/teaching/01-项目概述与整体架构.md`
- Create: `docs/teaching/02-LangGraph智能体编排.md`
- Create: `docs/teaching/03-Agent四层记忆系统.md`
- Create: `docs/teaching/04-多模态RAG检索实现.md`
- Create: `docs/teaching/05-RAGAS质量评估.md`
- Create: `docs/teaching/06-GSAP前端动效优化.md`
- Create: `docs/teaching/07-从开发到部署.md`

每篇 MD 结构：
```
# 标题

## 本节目标
一句话说明学完能掌握什么

## 核心概念
用 mermaid 图展示整体流程（严格按官方语法）

## 代码实现
关键代码片段 + 逐行解释

## 运行验证
怎么跑、怎么看效果

## 小结
本节要点回顾
```

- [ ] **Step 1-7: 逐一编写 7 篇教学 MD 文件**

每篇包含至少 2 个 mermaid 图（流程图 + 架构图）。

需严格遵守 mermaid 官方语法，确保可渲染。

- [ ] **Step 8: 提交所有教学文件**

```bash
git add docs/teaching/
git commit -m "docs: 添加教学 MD 文件"
```

---

### Task 6.2: 编写毕设大纲

**Files:**
- Create: `docs/teaching/毕设大纲.md`

大纲结构（已在设计文档中确认）：

```
# 基于 LangGraph 的多模态 RAG 电商导购 Agent 设计与实现

## 1. 绪论
   1.1 电商导购场景下的智能问答需求
   1.2 大模型 + RAG + Agent 的技术趋势
   1.3 论文主要工作与创新点

## 2. 相关技术与理论基础
   2.1 LangChain 与 LangGraph 框架（StateGraph）
   2.2 RAG 检索增强生成（CLIP + BGE-M3 + Qdrant + RRF）
   2.3 多 Agent 编排模式（ReAct / Plan & Solve / Reflection）
   2.4 四层记忆系统（工作/情景/语义/感知）
   2.5 RAGAS 质量评估体系
   2.6 GSAP 前端动画框架

## 3. 系统需求分析（业务/功能/非功能需求）

## 4. 系统总体设计
   4.1 系统架构设计
     - 4.1.1 前后端分离架构
     - 4.1.2 LangGraph StateGraph 编排设计
     - 4.1.3 多 Agent 编排架构（Intent → Plan → ReAct → Reflect）
     - 4.1.4 四层记忆系统架构
   4.2 技术选型与依赖关系
   4.3 接口契约设计（REST + SSE）
   4.4 Docker 容器化部署方案

## 5. 系统详细设计与实现
   5.1 意图识别模块实现
   5.2 Plan & Solve 计划生成实现
   5.3 基于 ReAct 的 Agent 循环实现
   5.4 Reflection 自反思修正实现
   5.5 多模态 RAG 检索实现（数据采集/向量化/混合检索/引用）
   5.6 四层记忆系统实现
   5.7 流式生成与澄清交互（SSE + Graph Interrupt）
   5.8 前端 GSAP 动效优化
   5.9 RAGAS 质量评估流水线

## 6. 系统测试与评估
   6.1 功能测试
   6.2 检索性能评测（Top-1 / Top-3 / Latency）
   6.3 RAGAS 综合质量评估
   6.4 压测与稳定性

## 7. 总结与展望
   7.1 工作总结
   7.2 不足与改进方向
```

- [ ] **Step 1: 编写毕设大纲**

- [ ] **Step 2: 提交**

```bash
git add docs/teaching/毕设大纲.md
git commit -m "docs: 添加毕业设计论文大纲"
```

---

## Phase 7: 最终验证与合并

### Task 7.1: 端到端验证

- [ ] **Step 1: Docker 启动全栈**

```bash
docker compose up --build -d
```

- [ ] **Step 2: 验证后端 API**

```bash
curl http://localhost/api/v1/health
curl -X POST http://localhost/api/v1/upload/image -F "file=@rag/data/images/test.jpg"
```

- [ ] **Step 3: 验证前端加载**

访问 `http://localhost`，确认 GSAP 动画正常。

- [ ] **Step 4: 运行 RAGAS 评估**

```bash
cd /home/user/projects/AgentProject/Agent && PYTHONPATH=$(pwd) python rag/eval/ragas_eval.py
```

### Task 7.2: 最终提交

- [ ] **Step 1: 推送到远程**

```bash
git push -u origin refactor/langgraph-agent
```

- [ ] **Step 2: 创建 PR 到 develop**

```bash
gh pr create --base develop --head refactor/langgraph-agent --title "refactor: LangGraph 多 Agent 编排 + GSAP + RAGAS" --body "详见设计文档 docs/superpowers/specs/2026-06-14-langgraph-refactor-design.md"
```