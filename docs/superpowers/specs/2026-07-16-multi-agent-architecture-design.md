# Multi-Agent 协作架构改造 Spec

> 日期：2026-07-16
> 状态：设计中 — 待评审
> 范围：将单体 LangGraph 状态机重构为多 Agent 协作架构

---

## 一、背景与动机

### 1.1 当前架构问题

当前系统采用**单体 LangGraph 状态机**（15 节点固定流水线），存在以下问题：

| 问题 | 具体表现 | 影响 |
|------|---------|------|
| **耦合度高** | 检索/推荐/知识问答共用同一状态图和 LLM 上下文 | 修改一处影响全局，难以独立迭代 |
| **上下文膨胀** | 所有节点共享同一个 prompt，LLM 需同时理解检索+推荐+知识 | token 浪费，生成质量下降 |
| **无法并行** | 图片检索和文本检索虽可并行，但知识检索必须等检索完成 | 串行延迟累加 |
| **容错性差** | 任一节点失败（如 rerank API 超时），整个流程中断 | 用户体验差 |
| **不可扩展** | 新增场景（如对比、比价、尺码推荐）需修改整个图 | 开发成本高 |
| **面试同质化** | 2026 年"LangGraph 状态机"已是标配，无差异化 | 简历缺乏亮点 |

### 1.2 Multi-Agent 改造目标

```
单体 Agent（15 节点）→ 4 个专职 Agent 协作

用户请求
  │
  ▼
┌─────────────────────────────────────────────┐
│         Orchestrator Agent (编排Agent)       │
│  • 意图识别 → 路由到对应 Agent               │
│  • 汇总各 Agent 结果 → 生成最终响应          │
│  • 处理 Agent 间依赖和时序                   │
└──────────┬──────────┬──────────┬────────────┘
           │          │          │
     ┌─────▼────┐ ┌───▼────┐ ┌───▼─────┐
     │ Retrieval │ │Recommend│ │Knowledge│
     │  Agent    │ │  Agent  │ │  Agent  │
     │ 检索Agent │ │推荐Agent │ │知识Agent │
     └──────────┘ └────────┘ └─────────┘
```

**量化目标**：
- 端到端延迟 P95 < 2000ms（当前 3151ms）
- 各 Agent 可独立部署/扩容/降级
- 新增场景只需新增 Agent，不改现有代码
- 检索 Agent 命中率 > 90%，推荐 Agent 可用率 > 85%

---

## 二、Agent 拆分设计

### 2.1 Agent 清单

| Agent | 职责 | 输入 | 输出 | 复杂度 |
|-------|------|------|------|--------|
| **Orchestrator** | 意图识别 → 路由 → 编排 → 汇总 | 用户请求（图+文） | 最终响应 | 高 |
| **Retrieval** | 多模态检索（图文混合 + 精排） | 图片向量/文本向量/spasm | 候选商品列表+分数 | 中 |
| **Recommendation** | 推荐理由生成 + 澄清对话 | 候选商品+用户画像 | 结构化推荐文案+引用 | 中 |
| **Knowledge** | 知识问答（科技参数/品牌知识） | 知识查询 | 知识片段+引用 | 低 |

### 2.2 Agent 间通信协议

```python
# 消息协议（类似 A2A / Agent Protocol）
@dataclass
class AgentMessage:
    message_id: str
    from_agent: str        # "orchestrator" | "retrieval" | "recommend" | "knowledge"
    to_agent: str
    task_id: str           # 关联到同一次用户请求
    action: str            # "invoke" | "result" | "error" | "stream"
    payload: dict          # Agent 间传递的数据
    timestamp: datetime
    
# 示例：Orchestrator → Retrieval Agent
AgentMessage(
    from_agent="orchestrator",
    to_agent="retrieval",
    task_id="req_123",
    action="invoke",
    payload={
        "image_embedding": [...],
        "text_embedding": [...],
        "slots": {"budget": [500, 1000], "category": "跑步鞋"},
        "top_k": 10,
    }
)

# 示例：Retrieval → Orchestrator
AgentMessage(
    from_agent="retrieval",
    to_agent="orchestrator",
    task_id="req_123",
    action="result",
    payload={
        "candidates": [...],
        "search_time_ms": 320,
        "fast_path_hit": True,
    }
)
```

### 2.3 共享状态设计

```python
# 基于 Redis 的跨 Agent 共享状态（替代 LangGraph 的内存 state）
class SharedState:
    """多 Agent 共享的持久化状态"""
    task_id: str
    session_id: str
    
    # Orchestrator 写入
    intent: str
    route_plan: list[str]           # ["retrieval", "recommend"]
    
    # Retrieval 写入
    candidates: list[dict]
    retrieval_done: bool
    retrieval_latency_ms: float
    
    # Recommendation 写入
    recommendation: str
    citations: list[dict]
    need_clarify: bool
    clarify_question: str
    
    # Knowledge 写入
    knowledge_answer: str
    knowledge_citations: list[dict]
    
    # 通用
    errors: dict[str, str]          # {agent_name: error_message}
    status: str                     # "pending" | "partial" | "complete" | "failed"
```

---

## 三、各 Agent 详细设计

### 3.1 Orchestrator Agent（编排 Agent）

**定位**：用户请求的入口和出口，负责理解意图、分配任务、汇总结果。

**Skills/Tools**：
```python
ORCHESTRATOR_TOOLS = ToolRegistry({
    # 路由工具
    "route_to_retrieval": {
        "description": "将检索任务分配给 Retrieval Agent",
        "parameters": {"image_embedding": "list", "text_embedding": "list", "slots": "dict"},
    },
    "route_to_recommendation": {
        "description": "将推荐生成任务分配给 Recommendation Agent",
        "parameters": {"candidates": "list", "user_profile": "dict"},
    },
    "route_to_knowledge": {
        "description": "将知识问答任务分配给 Knowledge Agent",
        "parameters": {"query": "str", "context": "dict"},
    },
    
    # 并行工具
    "parallel_invoke": {
        "description": "同时调用多个 Agent（用于无依赖的并行任务）",
        "parameters": {"agent_calls": "list[dict]"},
    },
    
    # 汇总工具
    "synthesize_response": {
        "description": "将多个 Agent 结果合成最终响应",
        "parameters": {"retrieval_result": "dict", "recommendation_result": "dict"},
    },
    
    # 澄清工具
    "handle_clarification": {
        "description": "决定是否需要向用户发起澄清问题",
        "parameters": {"candidates": "list", "scores": "list"},
    },
})
```

**行为逻辑**：
```
1. 接收用户请求（图片 + 文本）
2. 意图识别 → 决定路由策略：
   - "找同款" → Retrieval → Recommendation
   - "问参数" → Knowledge（并行：Retrieval 可选）
   - "对比两款" → Retrieval(×2并行) → Recommend
   - "随便看看" → Retrieval（top-k 大一些）→ Recommend
3. 分配任务，监控进度
4. 如果 Retrieval 返回低置信 → 触发澄清对话
5. 汇总所有 Agent 结果 → SSE 推送最终响应
```

**状态机**：
```python
class OrchestratorState(TypedDict):
    intent: str
    route_plan: list[str]           # 计划调用的 Agent 序列
    completed_agents: list[str]     # 已完成的 Agent
    pending_agents: list[str]       # 待执行的 Agent
    agent_results: dict[str, dict]  # {agent_name: result}
    need_clarify: bool
    final_answer: str
```

### 3.2 Retrieval Agent（检索 Agent）

**定位**：专职多模态检索，是系统性能的关键路径。

**Skills/Tools**：
```python
RETRIEVAL_TOOLS = ToolRegistry({
    # 基础检索
    "clip_search": {
        "description": "CLIP 视觉向量检索",
        "parameters": {"image_embedding": "list", "top_k": "int"},
        "impl": "rag.image_search.search_by_image",
    },
    "bge_search": {
        "description": "BGE-M3 语义向量检索",
        "parameters": {"text_embedding": "list", "top_k": "int"},
        "impl": "rag.text_retrieval.search_by_text",
    },
    "hybrid_search": {
        "description": "CLIP + BGE 混合检索 + RRF 融合",
        "parameters": {"image_embedding": "list", "text_embedding": "list", "top_k": "int"},
        "impl": "rag.hybrid_search.hybrid_search",
    },
    
    # 精排
    "rerank": {
        "description": "使用 gte-rerank-v2 精排候选商品",
        "parameters": {"query": "str", "candidates": "list"},
        "impl": "rag.rerank.rerank",
    },
    
    # 快速路径（Phase 1 已实现）
    "fast_path_search": {
        "description": "高置信场景跳过 rerank 的快速检索",
        "parameters": {"embedding": "list", "threshold": "float"},
        "impl": "rag.hybrid_search.hybrid_search",  # 内部自动判断
    },
})
```

**内部决策逻辑**：
```
输入: image_embedding, text_embedding, slots
  │
  ├── 纯图片 → clip_search
  │   └── CLIP > 0.85? → 直接返回（Phase 1 快速路径）
  │   └── CLIP ≤ 0.85? → rerank 精排
  │
  ├── 纯文本 → bge_search
  │   └── BGE > 0.75? → 直接返回
  │   └── BGE ≤ 0.75? → rerank 精排
  │
  └── 图文混合 → hybrid_search
      ├── CLIP > 0.90? → 只做 RRF，跳过 rerank
      └── CLIP ≤ 0.90? → RRF + rerank

输出: {"candidates": [...], "latency_ms": float, "fast_path": bool}
```

**特点**：
- 无 LLM 调用（纯向量检索 + API rerank），延迟最低
- 可独立水平扩展（Qdrant 分片）
- 快速路径命中率目标 > 50%

### 3.3 Recommendation Agent（推荐 Agent）

**定位**：基于候选商品和用户画像，生成推荐理由，处理澄清对话。

**Skills/Tools**：
```python
RECOMMEND_TOOLS = ToolRegistry({
    # 画像注入
    "load_user_profile": {
        "description": "从 Redis 加载用户画像（Phase 2 ICM）",
        "parameters": {"user_id": "str"},
        "impl": "app.memory.profile_store.ProfileStore.get",
    },
    
    # 商品详情获取
    "get_product_details": {
        "description": "获取商品完整参数和描述",
        "parameters": {"sku_ids": "list[str]"},
        "impl": "rag.db_client.get_product_details",
    },
    
    # 引用检索
    "get_citations": {
        "description": "获取商品知识片段作为引用",
        "parameters": {"sku_ids": "list[str]"},
        "impl": "rag.text_retrieval.get_citations_by_sku",
    },
    
    # 生成推荐理由
    "generate_recommendation": {
        "description": "基于候选+画像+引用生成推荐文案",
        "parameters": {"candidates": "list", "profile": "dict", "citations": "list"},
        "impl": "llm_call",  # 调用 DeepSeek 生成
    },
    
    # 澄清对话
    "decide_clarify": {
        "description": "判断是否需要发起澄清问题",
        "parameters": {"scores": "list", "slots": "dict"},
        "impl": "rule_engine",  # 规则判断，无需 LLM
    },
    "generate_clarify_question": {
        "description": "生成澄清问题（预算/场景/偏好三选一）",
        "parameters": {"missing_slots": "list[str]"},
        "impl": "template_fill",  # 模板填充
    },
})
```

**Prompt 模板（独立于其他 Agent）**：
```
你是运动鞋导购顾问。根据以下信息为用户生成推荐理由：

用户画像：{proference_context}

候选商品：
{candidates_with_details}

引用知识：
{citations}

要求：
1. 每个推荐商品引用至少一条知识片段
2. 结合用户画像中的偏好（预算/风格/品牌）
3. 简洁有力，不超过 150 字
4. 不确定时明确说明
```

### 3.4 Knowledge Agent（知识 Agent）

**定位**：回答用户关于商品科技参数、品牌知识、购买建议的问题。

**Skills/Tools**：
```python
KNOWLEDGE_TOOLS = ToolRegistry({
    # 知识检索
    "search_knowledge_base": {
        "description": "在 Qdrant citations 集合中检索知识片段",
        "parameters": {"query": "str", "top_k": "int"},
        "impl": "rag.text_retrieval.search_citations",
    },
    
    # 商品参数查询
    "get_product_specs": {
        "description": "查询商品详细参数",
        "parameters": {"sku_id": "str"},
        "impl": "rag.db_client.get_product_specs",
    },
    
    # 对比工具
    "compare_products": {
        "description": "对比两款商品的核心差异",
        "parameters": {"sku_a": "str", "sku_b": "str"},
        "impl": "rag.services.compare_products",
    },
    
    # 知识生成
    "generate_knowledge_answer": {
        "description": "基于检索知识生成回答",
        "parameters": {"query": "str", "context": "list[dict]"},
        "impl": "llm_call",
    },
})
```

**适用场景**：
- "䨻科技和飞电科技的差别是什么？"
- "韦德之道12有碳板吗？"
- "飞电6 ELITE 和 CHALLENGER 怎么选？"

---

## 四、新增 Skills/Tools 定义

### 4.1 完整工具清单

| 工具名 | 归属 Agent | 功能 | 实现方式 |
|--------|-----------|------|---------|
| `route_to_retrieval` | Orchestrator | 路由到检索 Agent | 消息队列 |
| `route_to_recommendation` | Orchestrator | 路由到推荐 Agent | 消息队列 |
| `route_to_knowledge` | Orchestrator | 路由到知识 Agent | 消息队列 |
| `parallel_invoke` | Orchestrator | 并行调用多个 Agent | asyncio.gather |
| `synthesize_response` | Orchestrator | 合成最终响应 | 模板合并 |
| `clip_search` | Retrieval | CLIP 视觉检索 | Qdrant |
| `bge_search` | Retrieval | BGE-M3 语义检索 | Qdrant |
| `hybrid_search` | Retrieval | 混合检索 + RRF | Qdrant + RRF |
| `rerank` | Retrieval | 精排 | DashScope API |
| `load_user_profile` | Recommendation | 加载用户画像 | Redis (ICM) |
| `get_product_details` | Recommendation | 商品参数查询 | Qdrant payload |
| `get_citations` | Recommendation | 片段检索 | Qdrant citations |
| `generate_recommendation` | Recommendation | 推荐文案生成 | DeepSeek LLM |
| `decide_clarify` | Recommendation | 澄清判断 | 规则引擎 |
| `generate_clarify_question` | Recommendation | 澄清问题生成 | 模板填充 |
| `search_knowledge_base` | Knowledge | 知识检索 | Qdrant |
| `get_product_specs` | Knowledge | 参数查询 | Qdrant |
| `compare_products` | Knowledge | 商品对比 | LLM |
| `generate_knowledge_answer` | Knowledge | 知识回答 | DeepSeek LLM |

### 4.2 Skill 注册表模式

```python
# backend/app/agent/skill_registry.py

class SkillRegistry:
    """技能注册表：每个 Agent 维护自己的技能列表"""
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self._skills: dict[str, Skill] = {}
    
    def register(self, name: str, description: str, parameters: dict, impl: Callable):
        self._skills[name] = Skill(name, description, parameters, impl)
    
    def get_openai_tools_format(self) -> list[dict]:
        """输出 OpenAI function calling 格式的 tools 定义"""
        return [
            {
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            param: {"type": "string", "description": desc}
                            for param, desc in skill.parameters.items()
                        },
                    },
                },
            }
            for skill in self._skills.values()
        ]
    
    async def execute(self, tool_name: str, parameters: dict) -> dict:
        """执行 Skill"""
        skill = self._skills.get(tool_name)
        if not skill:
            return {"error": f"Unknown skill: {tool_name}"}
        try:
            result = await skill.impl(**parameters)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}
```

---

## 五、关键挑战与解决方案

### 5.1 状态一致性

**问题**：4 个 Agent 各自有状态，如何保证一致？

**方案**：
- **共享状态层**：Redis Hash 存储 `SharedState`，所有 Agent 读写同一份
- **事件驱动更新**：Agent 发消息时更新状态，其他 Agent 通过 Pub/Sub 感知
- **最终一致性**：允许短暂不一致（如 Retrieval 先返回，Recommendation 稍后到达）

```python
# state/manager.py
class SharedStateManager:
    """基于 Redis 的跨 Agent 状态管理"""
    
    def __init__(self, redis_client, task_id: str):
        self.redis = redis_client
        self.key = f"multiagent:state:{task_id}"
    
    async def set_agent_result(self, agent_name: str, result: dict):
        """Agent 完成后写入结果"""
        pipeline = self.redis.pipeline()
        pipeline.hset(self.key, f"{agent_name}:result", json.dumps(result))
        pipeline.hset(self.key, f"{agent_name}:status", "done")
        pipeline.publish(f"multiagent:events:{task_id}", 
                        json.dumps({"agent": agent_name, "status": "done"}))
        await pipeline.execute()
    
    async def wait_for_agents(self, agent_names: list[str], timeout: float = 5.0) -> dict:
        """等待多个 Agent 完成"""
        results = {}
        for name in agent_names:
            result = await self._wait_for(f"{name}:status", "done", timeout)
            results[name] = result
        return results
```

### 5.2 SSE 多路复用

**问题**：前端只有一个 SSE 连接，4 个 Agent 如何协同推送？

**方案**：Orchestrator 作为 SSE 的唯一发送者，拦截各 Agent 的流式输出：

```python
# Orchestrator 内部
async def stream_to_client(self, queue: asyncio.Queue):
    """合并多个 Agent 的流式事件 → 单路 SSE"""
    while True:
        event_type, data = await queue.get()
        
        if event_type == "retrieval_candidates":
            await self.send_sse("candidates", data)
        elif event_type == "recommendation_delta":
            await self.send_sse("delta_text", data)
        elif event_type == "knowledge_delta":
            await self.send_sse("delta_text", data)
        elif event_type == "clarification":
            await self.send_sse("clarify", data)
        elif event_type == "final":
            await self.send_sse("final", data)
            break
```

### 5.3 容错与降级

**问题**：一个 Agent 挂了怎么办？

**方案**：

| 故障场景 | 降级策略 |
|---------|---------|
| Retrieval 失败 | 返回缓存结果 / 兜底热门商品 |
| Recommendation 失败 | 返回候选列表（无文案） |
| Knowledge 失败 | "暂无相关信息" + 引导重新提问 |
| Orchestrator 失败 | 直连 Retrieval（降级为单 Agent 模式） |

```python
# 容错装饰器
def agent_fallback(fallback_value: dict, timeout: float = 10.0):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Agent {func.__name__} timeout, using fallback")
                return fallback_value
            except Exception as e:
                logger.error(f"Agent {func.__name__} error: {e}")
                return fallback_value
        return wrapper
    return decorator
```

### 5.4 延迟优化

**问题**：多 Agent 协作是否增加延迟？

**方案**：
- **并行执行**：无依赖的 Agent 并行启动（如 Retrieval + Knowledge 同时跑）
- **流水线执行**：Retrieval 返回 top-10 后，Recommendation 立即开始（不等 rerank 全部完成）
- **快速路径**：Phase 1 的快速路径在 Retrieval Agent 内部生效，不影响架构
- **预期延迟**：Retrieval(300ms) + max(Recommendation(500ms), Knowledge(400ms)) ≈ 800ms

---

## 六、评测方案

### 6.1 评测维度

| 维度 | 指标 | 目标 |
|------|------|------|
| **协作效率** | 多 Agent 延迟 vs 单体延迟 | 多 Agent ≤ 单体 × 1.2 |
| **检索质量** | 多 Agent 召回率 vs 单体 | 差异 < 1pp |
| **生成质量** | 推荐文案 RAGAS 评分 | Faithfulness ≥ 0.80 |
| **容错性** | 单 Agent 降级后的成功率 | ≥ 95% |
| **扩展性** | 新增场景开发时间 | ≤ 1 天 |
| **Agent 协作** | 并行 Agent 占比 | ≥ 40% |

### 6.2 对比实验

```
实验组 A：单体 Agent（当前 LangGraph 15 节点）
实验组 B：多 Agent 协作（Orchestrator + 3 个子 Agent）

对比指标：
1. P50/P95/P99 延迟（分场景：同款识别/知识问答/对比）
2. Top-1/Top-3 命中率
3. RAGAS Faithfulness / Answer Relevancy
4. 单 Agent 注入故障后的成功率
5. token 消耗总量（多 Agent 是否增加了 LLM 调用）
```

---

## 七、实施计划

### Phase 3.1：基础设施（2-3 天）

| 任务 | 文件 | 产出 |
|------|------|------|
| Skill 注册表 | `backend/app/agent/skill_registry.py` | SkillRegistry 类 |
| 共享状态管理器 | `backend/app/agent/shared_state.py` | SharedStateManager (Redis) |
| 消息协议 | `backend/app/agent/message.py` | AgentMessage + 编解码 |
| 容错装饰器 | `backend/app/agent/reliability.py` | @agent_fallback |
| 单元测试 | `backend/tests/test_multi_agent_infra.py` | 10+ 测试用例 |

### Phase 3.2：子 Agent 实现（3-4 天）

| 任务 | 文件 | 产出 |
|------|------|------|
| Retrieval Agent | `backend/app/agent/retrieval_agent.py` | Agent + Skills |
| Recommendation Agent | `backend/app/agent/recommend_agent.py` | Agent + Skills |
| Knowledge Agent | `backend/app/agent/knowledge_agent.py` | Agent + Skills |
| 各 Agent 单元测试 | `backend/tests/test_*_agent.py` | 每 Agent 5+ 测试 |

### Phase 3.3：编排层实现（2-3 天）

| 任务 | 文件 | 产出 |
|------|------|------|
| Orchestrator Agent | `backend/app/agent/orchestrator_agent.py` | Agent + 路由逻辑 |
| SSE 多路复用 | `backend/app/agent/stream_merger.py` | 单路 SSE 推送 |
| Graph 集成 | `backend/app/graph/graph.py` | 调用 Orchestrator |
| API 测试 | 扩展 test_api_integration.py | 多 Agent 场景测试 |

### Phase 3.4：评测与优化（1-2 天）

| 任务 | 文件 | 产出 |
|------|------|------|
| 多 Agent 评测脚本 | `rag/scripts/eval/evaluate_multi_agent.py` | A/B 对比报告 |
| 容错测试 | `backend/tests/test_multi_agent_failover.py` | 故障注入测试 |
| 最终报告 | `docs/eval/multi_agent_report.md` | 完整评测数据 |

---

## 八、面试素材（完成后可写）

> **Multi-Agent 协作架构**：将单体 LangGraph 状态机（15 节点）重构为 4 个专职 Agent 协作架构：
> - **Orchestrator Agent**：负责意图识别、任务路由、结果汇总，基于 OpenAI function calling 实现灵活的 Agent 调度
> - **Retrieval Agent**：专职多模态检索（CLIP + BGE-M3 + RRF），内置高置信快速路径（命中率 50%，延迟降低 40%）
> - **Recommendation Agent**：结合跨会话用户画像（ICM）和商品知识生成个性化推荐，独立 prompt 模板
> - **Knowledge Agent**：处理科技参数/品牌知识问答，支持商品对比
> 
> **关键成果**：
> - 端到端 P95 延迟从 3151ms 降至 ~800ms（并行化 + 快速路径）
> - 各 Agent 可独立部署/扩容/降级，单 Agent 故障不影响整体服务
> - 新增场景（如商品对比、尺码推荐）只需新增 Agent，开发时间从 3 天降至 1 天
> - SSE 多路复用保证前端单连接，多 Agent 协同推送

---

## 九、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 多 Agent 延迟反而增加 | 用户体验下降 | 并行化 + 流水线执行，设置超时降级 |
| Redis 状态不一致 | Agent 间数据不同步 | 最终一致性 + 超时兜底 + 版本号 |
| Agent 间循环调用 | 死循环/成本飙升 | 硬限制最大调用深度（5 层）+ 调用图检测 |
| 单个 Agent 故障 | 部分功能不可用 |  graceful degradation + 热备份 |
| 测试复杂度高 | 难以覆盖所有 Agent 交互 | 基于 message 的 mock 测试 + 集成测试 |

---

## 十、文件变更清单

### 新增文件

```
backend/app/agent/
  skill_registry.py       # 技能注册表
  shared_state.py         # 跨 Agent 共享状态
  message.py              # Agent 间消息协议
  reliability.py          # 容错/降级装饰器
  retrieval_agent.py      # 检索 Agent
  recommend_agent.py      # 推荐 Agent
  knowledge_agent.py      # 知识 Agent
  orchestrator_agent.py   # 编排 Agent
  stream_merger.py        # SSE 多路复用

backend/tests/
  test_multi_agent_infra.py   # 基础设施测试
  test_retrieval_agent.py     # 检索 Agent 测试
  test_recommend_agent.py     # 推荐 Agent 测试
  test_knowledge_agent.py     # 知识 Agent 测试
  test_orchestrator_agent.py  # 编排 Agent 测试
  test_multi_agent_e2e.py     # 端到端集成测试

rag/scripts/eval/
  evaluate_multi_agent.py     # 多 Agent 评测脚本
```

### 修改文件

```
backend/app/graph/
  graph.py                # 改为调用 Orchestrator Agent
  nodes.py                # 拆分为独立 Agent

backend/app/
  main.py                 # 注册多 Agent 路由
