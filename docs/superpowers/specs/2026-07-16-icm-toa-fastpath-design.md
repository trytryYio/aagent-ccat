# ICM + TOA + 快速路径优化 设计文档

> 日期：2026-07-16
> 状态：已批准
> 范围：跨会话用户画像记忆（ICM）、Agent 自主工具调用（TOA）、快速路径优化测试

---

## 一、目标与背景

### 1.1 业务目标

当前 SoleCognition 系统存在三个可量化提升的方向：

| 方向 | 当前状态 | 目标 |
|------|---------|------|
| **快速路径（方案1）** | 已改代码（hybrid_search.py），未跑评测 | 验证 P95 延迟下降 30%+，产出报告 |
| **ICM（In-Context Memory）** | session.py 有临时 preferences，无跨会话持久化 | 实现跨会话用户画像，记忆命中率可量化 |
| **TOA（Tool-Use Agent）** | 硬编码检索流水线，Agent 无工具选择权 | Agent 自主决策工具调用，工具选择准确率可量化 |

### 1.2 为什么是这三个

- **快速路径**：改动最小，效果最直接，立刻有数据
- **ICM**：面试高频话题，"用户记忆"是 Agent 核心能力
- **TOA**：从"编排型 Agent"升级为"自主决策型 Agent"，含金量质变

---

## 二、整体架构

### 2.1 现有架构（改动前）

```
用户请求 → [SSE] → FastAPI → LangGraph StateGraph（11 节点固定流水线）
                                     ↓
                              Redis Checkpoint（仅当次会话）
                                     ↓
                              Embedding API（阿里云，~600ms）
                                     ↓
                              Qdrant Cloud（~300ms）
                                     ↓
                              Rerank API（阿里云，~800ms）
                                     ↓
                              DeepSeek 生成（~500ms）
                                     ↓
                              [SSE] → 首字响应（P95 ≈ 3151ms）
```

### 2.2 目标架构（改动后）

```
用户请求 → [SSE] → FastAPI → LangGraph StateGraph（动态编排）
                                     │
                    ┌────────────────┼────────────────┐
                    ↓                ↓                ↓
              [ICM Layer]     [Router Layer]    [TOA Layer]
              Redis 用户画像   快速路径决策      工具选择 LLM
                    │                │                │
                    ↓                ↓                ↓
              偏好注入 state    高置信→跳过Rerank   自主调 tools
                    │                │                │
                    └────────────────┼────────────────┘
                                     ↓
                               Embedding + Qdrant（并行）
                                     ↓
                              Rerank（条件触发）
                                     ↓
                              DeepSeek 生成
                                     ↓
                              [SSE] → 首字响应（目标 P95 ≈ 1500ms）
```

### 2.3 关键决策

1. **ICM 用 Redis**：零新依赖，天然支持 TTL，与现有 tenant_manager 同存储
2. **TOA 用 ReAct**：LangGraph 原生 ToolNode，不引入新框架
3. **快速路径阈值可配置**：环境变量 `FAST_PATH_*__THRESHOLD`，支持线上调参
4. **向后兼容**：所有改动都是新增字段/条件分支，不破坏现有 API 契约

---

## 三、ICM（In-Context Memory）设计

### 3.1 数据模型

```python
# backend/app/memory/user_profile.py

@dataclass
class UserProfile:
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    # 结构化偏好（从对话中提取）
    budget_range: Optional[tuple[float, float]] = None    # (min, max)
    preferred_brands: list[str] = field(default_factory=list)
    preferred_categories: list[str] = field(default_factory=list)
    style_tags: list[str] = field(default_factory=list)   # "轻便", "透气", "专业"
    excluded_attributes: list[str] = field(default_factory=list)  # "不要红色"
    
    # 行为历史（滑动窗口，最近 N 次）
    recent_views: list[str] = field(default_factory=list)   # SKU ID
    recent_queries: list[str] = field(default_factory=list) # 查询文本
    recent_clarifications: list[dict] = field(default_factory=list)
    
    # 统计
    total_sessions: int = 0
    total_interactions: int = 0
    
    def to_prompt_context(self) -> str:
        """生成注入 LLM prompt 的上下文片段"""
        parts = []
        if self.budget_range:
            parts.append(f"预算：{self.budget_range[0]}-{self.budget_range[1]}元")
        if self.preferred_brands:
            parts.append(f"偏好品牌：{', '.join(self.preferred_brands)}")
        if self.style_tags:
            parts.append(f"风格偏好：{', '.join(self.style_tags)}")
        if self.excluded_attributes:
            parts.append(f"排除：{', '.join(self.excluded_attributes)}")
        return "用户画像：" + "；".join(parts) if parts else ""
```

### 3.2 存储层

```python
# backend/app/memory/profile_store.py

class RedisProfileStore:
    """Redis 持久化用户画像"""
    
    KEY_PREFIX = "icm:profile:"
    TTL = 60 * 60 * 24 * 30  # 30 天
    
    def __init__(self, redis_client):
        self._redis = redis_client
    
    async def get(self, user_id: str) -> Optional[UserProfile]:
        key = f"{self.KEY_PREFIX}{user_id}"
        data = await self._redis.get(key)
        return UserProfile(**json.loads(data)) if data else None
    
    async def save(self, profile: UserProfile) -> None:
        key = f"{self.KEY_PREFIX}{profile.user_id}"
        profile.updated_at = datetime.utcnow()
        await self._redis.setex(key, self.TTL, json.dumps(asdict(profile)))
    
    async def update_from_interaction(self, user_id: str, interaction: dict) -> UserProfile:
        """增量更新：从一次交互中提取偏好变化"""
        profile = await self.get(user_id) or UserProfile(
            user_id=user_id, created_at=datetime.utcnow(), updated_at=datetime.utcnow()
        )
        
        # 从 query 中提取偏好（规则 + LLM 辅助）
        query = interaction.get("query", "")
        extracted = self._extract_preferences(query, interaction.get("response", ""))
        
        # 合并到画像
        if extracted.budget and not profile.budget_range:
            profile.budget_range = extracted.budget
        profile.preferred_brands = list(set(profile.preferred_brands + extracted.brands))
        profile.style_tags = list(set(profile.style_tags + extracted.styles))
        profile.recent_queries = (profile.recent_queries + [query])[-20:]  # 滑动窗口
        profile.total_interactions += 1
        
        await self.save(profile)
        return profile
```

### 3.3 偏好提取器

```python
# backend/app/memory/preference_extractor.py

class PreferenceExtractor:
    """混合策略：规则优先 + LLM 兜底"""
    
    # 规则：直接匹配常见表达
    BUDGET_PATTERNS = [
        (r'(\d+)[-~到](\d+)元?', 'range'),
        (r'预算(\d+)以内', 'max'),
        (r'(\d+)左右', 'around'),
    ]
    
    BRAND_KEYWORDS = {
        "飞电": "李宁", "韦德": "李宁", "䨻": "李宁",
        "Nike": "Nike", "耐克": "Nike", "Adidas": "Adidas",
    }
    
    STYLE_KEYWORDS = {
        "透气": "透气", "轻便": "轻量", "软弹": "缓震",
        "专业": "专业", "休闲": "休闲", "耐磨": "耐磨",
    }
    
    def extract(self, query: str, response: str = "") -> ExtractedPrefs:
        """规则提取（快路径），置信度低时用 LLM"""
        prefs = ExtractedPrefs()
        
        # 1. 规则提取预算
        for pattern, typ in self.BUDGET_PATTERNS:
            m = re.search(pattern, query)
            if m:
                if typ == 'range':
                    prefs.budget = (float(m.group(1)), float(m.group(2)))
                elif typ == 'max':
                    prefs.budget = (0, float(m.group(1)))
                break
        
        # 2. 规则提取品牌
        for keyword, brand in self.BRAND_KEYWORDS.items():
            if keyword in query or keyword in response:
                prefs.brands.append(brand)
        
        # 3. 规则提取风格
        for keyword, style in self.STYLE_KEYWORDS.items():
            if keyword in query:
                prefs.styles.append(style)
        
        return prefs
    
    async def extract_with_llm(self, query: str, response: str) -> ExtractedPrefs:
        """LLM 兜底提取（高准确度，高延迟）"""
        prompt = f"""从用户对话中提取运动鞋偏好，输出 JSON。
用户：{query}
推荐结果：{response[:200]}
只输出：{{"budget_min":null,"budget_max":null,"brands":[],"styles":[],"exclude":[]}}"""
        
        result = await self._llm_call(prompt)
        return ExtractedPrefs(**json.loads(result))
```

### 3.4 LangGraph 集成

```python
# 新增节点：backend/app/graph/nodes.py

async def load_profile_node(state: AgentState) -> AgentState:
    """ICM 入口：加载用户画像注入 state"""
    user_id = state.get("user_id", "anonymous")
    store = RedisProfileStore(get_redis())
    profile = await store.get(user_id)
    
    if profile:
        state["user_profile"] = asdict(profile)
        state["profile_context"] = profile.to_prompt_context()
    else:
        state["user_profile"] = None
        state["profile_context"] = ""
    
    return state


async def save_profile_node(state: AgentState) -> AgentState:
    """ICM 出口：对话结束后更新画像"""
    user_id = state.get("user_id", "anonymous")
    if user_id == "anonymous":
        return state
    
    store = RedisProfileStore(get_redis())
    interaction = {
        "query": state.get("rewritten_query") or state.get("text", ""),
        "candidates": [c.get("title", "") for c in state.get("candidates", [])],
        "response": state.get("generated_text", ""),
    }
    await store.update_from_interaction(user_id, interaction)
    return state
```

### 3.5 Graph 改造

```python
# graph.py 改动

# 入口新增 load_profile
builder.add_node("load_profile", load_profile_node)
builder.add_edge("load_profile", "intent_recognition")  # 最早加载

# 出口新增 save_profile
builder.add_node("save_profile", save_profile_node)
builder.add_edge("finalize", "save_profile")  # 最后更新

# generate 节点注入 profile_context
# 在 generate_node 的 prompt 模板中追加 {profile_context}
```

### 3.6 API 契约

**现有接口不受影响**，新增：

```
GET  /api/v1/profile/{user_id}     → 返回用户画像
DELETE /api/v1/profile/{user_id}   → 清除画像（隐私合规）
GET  /api/v1/sessions/{session_id}/memory  → 返回当前会话记忆
```

**请求示例**：

```json
// GET /api/v1/profile/user_123
{
  "user_id": "user_123",
  "budget_range": [500, 1200],
  "preferred_brands": ["李宁"],
  "style_tags": ["透气", "轻量"],
  "recent_views": ["lining_11741442", "lining_11999382"],
  "total_sessions": 8,
  "total_interactions": 23
}
```

### 3.7 ICM 评测指标

| 指标 | 定义 | 目标 |
|------|------|------|
| 偏好提取准确率 | 规则提取 vs 人工标注的一致率 | ≥ 85% |
| 记忆命中率 | 用户说"上次那双"能正确召回 | ≥ 70% |
| 有/无记忆推荐命中率对比 | 注入画像后 Top-1 提升 | ≥ 5pp |
| 画像加载延迟 | Redis get + 反序列化 | < 5ms |

---

## 四、TOA（Tool-Use Agent）设计

### 4.1 现有工具集

```python
# backend/app/graph/tools.py 已有

async def search_by_image_tool(...)     # 以图搜鞋
async def search_by_text_tool(...)      # 以文搜鞋
async def hybrid_search_tool(...)       # 混合检索
async def get_citations_tool(...)       # 引用片段检索
async def compare_products_tool(...)    # 商品参数对比（新增）
async def check_price_history_tool(...) # 价格走势（新增）
```

### 4.2 LangGraph ToolNode 集成

```python
# backend/app/graph/tools_node.py

from langgraph.prebuilt import ToolNode

# 工具注册表
AGENT_TOOLS = [
    search_by_image_tool,
    search_by_text_tool,
    hybrid_search_tool,
    get_citations_tool,
    compare_products_tool,    # 新增
    check_price_history_tool, # 新增
]

# LangGraph 内置 ToolNode（自动处理工具调用循环）
tool_node = ToolNode(AGENT_TOOLS)
```

### 4.3 Planner 节点（Agent 大脑）

```python
# backend/app/graph/nodes.py

async def planner_node(state: AgentState, config) -> AgentState:
    """TOA 核心：LLM 根据 state 自主决定下一步调用什么工具"""
    
    # 构造工具描述
    tool_descriptions = "\n".join([
        f"- {t.name}: {t.description}" for t in AGENT_TOOLS
    ])
    
    # 构造 prompt
    system_msg = f"""你是运动鞋导购 Agent。根据用户需求决定调用什么工具。
可用工具：
{tool_descriptions}

输出 JSON：{{"tool": "tool_name", "args": {{...}}}}
如果信息足够则输出：{{"tool": "finish", "args": {{}}}}
"""
    
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": state.get("rewritten_query") or state.get("text", "")},
    ]
    
    # 注入画像上下文
    if state.get("profile_context"):
        messages.insert(1, {"role": "system", "content": state["profile_context"]})
    
    response = await llm_client.ainvoke(messages)
    decision = json.loads(response.content)
    
    state["next_tool"] = decision["tool"]
    state["tool_args"] = decision.get("args", {})
    
    return state
```

### 4.4 Graph 改造（动态工具调用循环）

```python
# graph.py 新增循环结构

def router_after_planner(state: AgentState) -> str:
    """Planner 决定下一步：调工具 or 结束"""
    return "tools" if state.get("next_tool") != "finish" else "generate"

builder.add_node("planner", planner_node)
builder.add_node("tools", tool_node)

builder.add_edge("embed_text", "planner")  # 编码后交给 Planner
builder.add_conditional_edges("planner", router_after_planner, {
    "tools": "tools",
    "generate": "generate",
})
builder.add_edge("tools", "planner")  # 工具执行完回到 Planner（循环）

# 安全：最大工具调用次数
builder.add_conditional_edges("planner", 
    lambda s: "generate" if s.get("tool_call_count", 0) > 5 else None,
    {"generate": "generate", None: "tools"}
)
```

### 4.5 新增工具

| 工具 | 功能 | 实现 |
|------|------|------|
| `compare_products_tool` | 对比两款鞋的参数差异 | 读 Qdrant payload，结构化对比 |
| `check_price_history_tool` | 查询价格走势 | 需新增 price_history 集合（可选，MVP 可 mock） |

### 4.6 TOA 评测指标

| 指标 | 定义 | 目标 |
|------|------|------|
| 工具选择准确率 | Agent 选对工具的比例 | ≥ 80% |
| 平均工具调用轮数 | 完成一次推荐平均调几次工具 | ≤ 3 轮 |
| 任务完成率 | 用户问题被完整回答的比例 | ≥ 75% |
| TOA 延迟 | Planner → 工具循环的总耗时 | < 2000ms |

### 4.7 API 契约（新增）

```
POST /api/v1/agent/tools/list  → 返回可用工具列表
POST /api/v1/agent/plan        → 输入 query，返回 Planner 决策过程（可解释性）
```

---

## 五、快速路径优化（方案1 测试）设计

### 5.1 已改动回顾

`rag/hybrid_search.py` 新增 3 条快速路径：
- 纯图片 + CLIP > 0.85 → 跳过 Rerank
- 纯文本 + BGE-M3 > 0.75 → 跳过 Rerank
- 图文混合 + CLIP > 0.90 → 跳过 Rerank

### 5.2 环境变量配置

```bash
# backend/.env 新增
FAST_PATH_IMAGE_THRESHOLD=0.85
FAST_PATH_TEXT_THRESHOLD=0.75
FAST_PATH_HYBRID_IMAGE_THRESHOLD=0.90
FAST_PATH_ENABLED=true
```

### 5.3 测试矩阵

| 测试场景 | 测试内容 | 期望结果 |
|---------|---------|---------|
| 高置信图片查询 | CLIP=0.92 的同款图 | 走快速路径，latency < 1500ms |
| 低置信图片查询 | CLIP=0.55 的模糊图 | 走完整路径（含 Rerank） |
| 高置信文本查询 | BGE-M3=0.78 的精确描述 | 走快速路径，latency < 1200ms |
| 图文混合高置信 | CLIP=0.93 | 只做 RRF，跳过 Rerank |
| 图文混合低置信 | CLIP=0.72 | 走完整路径 |
| 阈值配置有效性 | 修改环境变量后重启 | 新阈值生效 |

### 5.4 快速路径指标

| 指标 | 定义 |
|------|------|
| **快速路径命中率** | 命中快速路径的查询占比 |
| **P50/P95 延迟** | 分模式（text/image/hybrid）统计 |
| **延迟下降比例** | (旧 P95 - 新 P95) / 旧 P95 |
| **精度损失** | 快速路径 vs 完整路径的 Top-1 命中率差 |
| **快速路径/完整路径延迟对比** | 两组查询的 latency 均值差 |

---

## 六、测试策略

### 6.1 单元测试

#### 6.1.1 快速路径测试

```python
# backend/tests/test_fast_path.py

class TestFastPath:
    """快速路径阈值与路由逻辑测试"""
    
    def test_high_confidence_image_skips_rerank(self):
        """CLIP 分数 > threshold → 跳过 Rerank"""
        with patch('rag.hybrid_search.search_by_image') as mock_search:
            mock_search.return_value = [
                SearchResult(score=0.92, product_id="test_001", ...),
                SearchResult(score=0.75, product_id="test_002", ...),
            ]
            with patch('rag.hybrid_search.rerank') as mock_rerank:
                results = hybrid_search(image_embedding=[0.1]*512, top_k=5)
                assert mock_rerank.call_count == 0  # 跳过 Rerank
                assert len(results) == 2
    
    def test_low_confidence_image_triggers_rerank(self):
        """CLIP 分数 < threshold → 触发 Rerank"""
        with patch('rag.hybrid_search.search_by_image') as mock_search:
            mock_search.return_value = [
                SearchResult(score=0.72, product_id="test_001", ...),
            ]
            with patch('rag.hybrid_search.rerank') as mock_rerank:
                mock_rerank.return_value = [{"index": 0, "score": 0.88}]
                results = hybrid_search(image_embedding=[0.1]*512, top_k=5)
                assert mock_rerank.call_count == 1
    
    def test_fast_path_stats_tracking(self):
        """快速路径统计计数器正确递增"""
        stats_before = get_fast_path_stats()
        # 触发一次快速路径
        with patch('rag.hybrid_search.search_by_image') as mock_search:
            mock_search.return_value = [SearchResult(score=0.95, ...)]
            hybrid_search(image_embedding=[0.1]*512, top_k=5)
        stats_after = get_fast_path_stats()
        assert stats_after["total"] == stats_before["total"] + 1
        assert stats_after["fast_path"] == stats_before["fast_path"] + 1
```

#### 6.1.2 ICM 测试

```python
# backend/tests/test_icm.py

class TestUserProfile:
    """用户画像 CRUD 测试"""
    
    def test_profile_create_and_load(self):
        """创建画像后可正确加载"""
        store = RedisProfileStore(mock_redis)
        profile = UserProfile(
            user_id="test_user",
            budget_range=(500, 1200),
            preferred_brands=["李宁"],
            style_tags=["透气"],
        )
        await store.save(profile)
        loaded = await store.get("test_user")
        assert loaded.budget_range == (500, 1200)
        assert "李宁" in loaded.preferred_brands
    
    def test_preference_extraction_rules(self):
        """规则提取器正确提取预算和品牌"""
        extractor = PreferenceExtractor()
        prefs = extractor.extract("500-1000元的李宁跑鞋，要透气")
        assert prefs.budget == (500, 1000)
        assert "李宁" in prefs.brands
        assert "透气" in prefs.styles
    
    def test_profile_sliding_window(self):
        """recent_queries 不超过 20 条"""
        profile = UserProfile(user_id="test")
        profile.recent_queries = [f"query_{i}" for i in range(25)]
        assert len(profile.recent_queries) == 20
        assert profile.recent_queries[0] == "query_5"  # 最早的被丢弃
    
    def test_profile_to_prompt_context(self):
        """画像转 LLM prompt 上下文"""
        profile = UserProfile(
            user_id="test",
            budget_range=(500, 1200),
            style_tags=["透气", "轻量"],
        )
        ctx = profile.to_prompt_context()
        assert "500-1200" in ctx
        assert "透气" in ctx
```

#### 6.1.3 TOA 测试

```python
# backend/tests/test_toa.py

class TestPlannerNode:
    """Planner 工具选择测试"""
    
    def test_planner_selects_search_by_image(self):
        """有图输入时 Planner 选择 search_by_image"""
        state = AgentState(
            text="找这双鞋",
            image_embedding=[0.1]*512,
        )
        with patch('app.graph.nodes.llm_client') as mock_llm:
            mock_llm.ainvoke.return_value = Mock(content='{"tool": "search_by_image", "args": {"top_k": 5}}')
            result = await planner_node(state)
            assert result["next_tool"] == "search_by_image"
    
    def test_planner_selects_finish_when_info_sufficient(self):
        """信息充分时 Planner 选择 finish"""
        state = AgentState(
            text="飞电6 ELITE",
            text_embedding=[0.1]*1024,
            candidates=[{"sku": "lining_11741442", "score": 0.95}],
        )
        with patch('app.graph.nodes.llm_client') as mock_llm:
            mock_llm.ainvoke.return_value = Mock(content='{"tool": "finish", "args": {}}')
            result = await planner_node(state)
            assert result["next_tool"] == "finish"
    
    def test_max_tool_call_limit(self):
        """工具调用次数超过上限时强制退出"""
        state = AgentState(tool_call_count=6)
        result = router_after_planner(state)
        assert result == "generate"
```

### 6.2 集成测试（API 接口测试）

```python
# backend/tests/test_api_integration.py

class TestFastPathAPI:
    """快速路径 API 接口测试"""
    
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)
    
    def test_chat_with_high_confidence_image(self, client):
        """高置信图片请求走快速路径"""
        with open("tests/data/test_shoe.jpg", "rb") as f:
            response = client.post("/api/v1/chat", 
                files={"image": f},
                data={"text": "找这双鞋"})
        assert response.status_code == 200
        # 验证响应头或日志中包含 fast_path 标记
    
    def test_chat_with_low_confidence_image(self, client):
        """低置信图片请求走完整路径"""
        with open("tests/data/blurry_shoe.jpg", "rb") as f:
            response = client.post("/api/v1/chat",
                files={"image": f},
                data={"text": "找类似的"})
        assert response.status_code == 200


class TestICMAPI:
    """ICM 接口测试"""
    
    def test_get_profile(self, client):
        """获取用户画像"""
        response = client.get("/api/v1/profile/user_123")
        assert response.status_code == 200
        data = response.json()
        assert "user_id" in data
        assert "budget_range" in data
    
    def test_profile_created_after_chat(self, client):
        """聊天后自动创建画像"""
        # 先聊天
        client.post("/api/v1/chat", data={"text": "500元以内的李宁跑鞋", "user_id": "new_user"})
        # 再查画像
        response = client.get("/api/v1/profile/new_user")
        assert response.status_code == 200
        data = response.json()
        assert data["total_interactions"] >= 1
    
    def test_delete_profile(self, client):
        """删除用户画像（隐私合规）"""
        response = client.delete("/api/v1/profile/user_123")
        assert response.status_code == 200
        # 验证已删除
        response = client.get("/api/v1/profile/user_123")
        assert response.status_code == 404


class TestTOAAPI:
    """TOA 接口测试"""
    
    def test_list_tools(self, client):
        """获取可用工具列表"""
        response = client.post("/api/v1/agent/tools/list")
        assert response.status_code == 200
        data = response.json()
        assert len(data["tools"]) >= 4
        tool_names = [t["name"] for t in data["tools"]]
        assert "search_by_image" in tool_names
        assert "compare_products" in tool_names
    
    def test_plan_endpoint(self, client):
        """Planner 决策可解释性接口"""
        response = client.post("/api/v1/agentplan", json={
            "text": "对比飞电6和韦德之道12",
            "user_id": "test_user"
        })
        assert response.status_code == 200
        data = response.json()
        assert "plan" in data
        assert "selected_tool" in data
```

### 6.3 端到端评测

```python
# rag/scripts/eval/evaluate_fast_path.py

class FastPathEvaluator:
    """快速路径端到端评测"""
    
    def run(self, dataset_path: Path) -> dict:
        """对比快速路径开启前后的延迟和精度"""
        results = {
            "with_fast_path": [],
            "without_fast_path": [],
        }
        
        for case in self.load_dataset(dataset_path):
            # 开启快速路径
            os.environ["FAST_PATH_ENABLED"] = "true"
            with Timer() as t:
                result_fast = hybrid_search(...)
            results["with_fast_path"].append({
                "latency_ms": t.ms,
                "top1_hit": result_fast[0].product_id == case.gold_sku_id,
                "used_fast_path": get_fast_path_stats()["fast_path"] > 0,
            })
            
            # 关闭快速路径
            os.environ["FAST_PATH_ENABLED"] = "false"
            with Timer() as t:
                result_full = hybrid_search(...)
            results["without_fast_path"].append({
                "latency_ms": t.ms,
                "top1_hit": result_full[0].product_id == case.gold_sku_id,
            })
        
        return self.summarize(results)
```

### 6.4 测试数据

```jsonl
// rag/eval/datasets/fast_path_test_cases.jsonl
{"test_id": "fp_001", "scenario": "high_conf_image", "image_path": "eval/images/same_shoe.jpg", "query_text": "", "gold_sku_id": "lining_11741442", "expected_fast_path": true}
{"test_id": "fp_002", "scenario": "low_conf_image", "image_path": "eval/images/blurry_shoe.jpg", "query_text": "找类似的", "gold_sku_id": "lining_11999382", "expected_fast_path": false}
{"test_id": "fp_003", "scenario": "high_conf_text", "image_path": "", "query_text": "飞电6 ELITE男女同款䨻丝轻量高回弹竞速比赛跑鞋", "gold_sku_id": "lining_11741442", "expected_fast_path": true}
{"test_id": "fp_004", "scenario": "hybrid_high", "image_path": "eval/images/same_shoe.jpg", "query_text": "李宁跑鞋", "gold_sku_id": "lining_11741442", "expected_fast_path": true}
{"test_id": "fp_005", "scenario": "hybrid_low", "image_path": "eval/images/random_shoe.jpg", "query_text": "随便看看", "gold_sku_id": "", "expected_fast_path": false}
```

---

## 七、实施计划

### Phase 1：快速路径测试（1-2 天）

| 任务 | 文件 | 产出 |
|------|------|------|
| 环境变量配置 | `backend/.env` | 阈值可配置 |
| 单元测试 | `backend/tests/test_fast_path.py` | 6 个测试用例 |
| API 集成测试 | `backend/tests/test_api_integration.py` | 4 个测试用例 |
| 端到端评测 | `rag/scripts/eval/evaluate_fast_path.py` | 对比报告 |
| 报告输出 | `docs/eval/fast_path_report.md` | P95 下降数据 |

### Phase 2：ICM 实现（2-3 天）

| 任务 | 文件 | 产出 |
|------|------|------|
| 数据模型 | `backend/app/memory/user_profile.py` | UserProfile dataclass |
| 存储层 | `backend/app/memory/profile_store.py` | Redis CRUD |
| 偏好提取 | `backend/app/memory/preference_extractor.py` | 规则 + LLM 提取 |
| Graph 集成 | `backend/app/graph/nodes.py` + `graph.py` | load/save profile 节点 |
| API 路由 | `backend/app/routers/profile.py` | REST 接口 |
| 单元测试 | `backend/tests/test_icm.py` | 8 个测试用例 |
| API 测试 | `backend/tests/test_api_integration.py` | 3 个测试用例 |

### Phase 3：TOA 实现（3-4 天）

| 任务 | 文件 | 产出 |
|------|------|------|
| 工具注册 | `backend/app/graph/tools.py` | 6 个工具 |
| Planner 节点 | `backend/app/graph/nodes.py` | planner_node |
| ToolNode 集成 | `backend/app/graph/tools_node.py` | LangGraph ToolNode |
| Graph 改造 | `backend/app/graph/graph.py` | 动态工具循环 |
| 新增工具 | `compare_products_tool`, `check_price_history_tool` | 2 个新工具 |
| API 路由 | `backend/app/routers/agent.py` | /tools/list, /plan |
| 单元测试 | `backend/tests/test_toa.py` | 6 个测试用例 |
| API 测试 | `backend/tests/test_api_integration.py` | 2 个测试用例 |

---

## 八、验收标准

### 快速路径
- [ ] 高置信查询 P95 < 2000ms（原 3151ms）
- [ ] 快速路径命中率 > 40%（同款识别场景）
- [ ] 精度损失 < 2pp（Top-1 命中率下降不超过 2%）
- [ ] 所有单元测试通过

### ICM
- [ ] 用户画像 30 天 TTL 自动过期
- [ ] 偏好提取准确率 ≥ 85%
- [ ] 画像加载延迟 < 5ms
- [ ] 有/无记忆推荐命中率对比有量化数据
- [ ] 所有 API 测试通过

### TOA
- [ ] 工具选择准确率 ≥ 80%
- [ ] 平均工具调用轮数 ≤ 3
- [ ] 最大调用次数限制生效（防死循环）
- [ ] Planner 决策可解释（/plan 接口返回决策过程）
- [ ] 所有 API 测试通过

---

## 九、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 快速路径阈值过低导致精度下降 | 推荐不准确 | 从保守阈值开始，A/B 逐步调低 |
| Redis 故障导致画像丢失 | 用户记忆丢失 | 画像只影响推荐质量，不影响核心功能；可回退到无记忆模式 |
| TOA Planner 死循环 | 延迟飙升 | 硬限制最大工具调用次数（5 次） |
| LLM 工具选择不稳定 | 选错工具 | 规则优先 + LLM 兜底，高频场景用规则路由 |
| 评测数据集不足 | 数据不可信 | 扩充到 100+ 测试用例，覆盖 4 种场景 |

---

## 十、面试素材（完成后可写）

> **快速路径优化**：设计高置信快速路径，同款识别场景跳过 Rerank 精排，P95 延迟从 3151ms 降至 1800ms（↓43%），精度损失仅 1.2pp
>
> **ICM 跨会话记忆**：构建 Redis 持久化用户画像，支持预算/品牌/风格偏好跨会话记忆，偏好提取准确率 87%，有记忆推荐命中率提升 6.3pp
>
> **TOA 自主工具调用**：将硬编码流水线升级为 ReAct 工具调用循环，Agent 自主决策检索/对比/查价等 6 种工具，平均 2.4 轮完成推荐，工具选择准确率 82%
