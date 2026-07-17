# 多维度过滤方案设计

**日期**: 2026-07-15
**目标**: 将近似替代场景的 Recall@K 从 20% 提升到 80%+
**现状**: 26 条评测用例，Recall@K=52.56%，近似替代场景 Top-1=20%

## 问题根因

近似替代场景全部是预算约束查询（如"预算600元以内 休闲鞋"）。当前流程：

1. embed("预算600元以内 休闲鞋") → 向量
2. 向量检索 top-10（不限价格）
3. PriceFilter 后置过滤

**问题**：价格过滤在检索后，如果 top-10 全是超预算商品，gold 商品永远找不到。

## 改动范围

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `rag/category_mapping.py` | **新增** | 品类别名映射表 + 层级匹配逻辑 |
| `backend/app/graph/slot_filling.py` | 修改 | 增强槽位提取：系列、功能、科技、品类别名展开 |
| `rag/text_retrieval.py` | 修改 | 接收完整 filter 参数，构建 Qdrant 硬过滤 |
| `backend/app/graph/nodes.py` | 修改 | search_node 传递 slots 到检索层 |
| `rag/hybrid_search.py` | 修改 | 接收 filter 参数，传递给底层检索 |
| `rag/postprocessors.py` | 修改 | 新增功能/科技后置软过滤 |

## 架构设计

### 数据流

```
用户输入: "预算600元以内 轻量透气的赤兔跑步鞋"
         ↓
Slot Filling 提取:
  budget_max=600, category=跑步鞋, series=赤兔,
  category_aliases=["跑步鞋","竞技跑鞋","减震跑鞋",...],
  functional=["轻量","透气"], tech_preferences=[]
         ↓
Qdrant 硬过滤 (检索前):
  price <= 600
  category MATCH_TEXT ["跑步鞋","竞技跑鞋",...]
  series MATCH_TEXT "赤兔"
  tenant_id = default
         ↓
向量检索 top_k * 3 (over-recall)
         ↓
后置软过滤 (检索后):
  name 包含 "轻量" 或 "透气" (功能匹配)
  DynamicTopK 截断
  ConfidenceGate 置信度门控
         ↓
安全回退: 过滤后 < 2 条时回退到无过滤结果
```

### 过滤维度分层

| 维度 | 过滤时机 | Qdrant 方法 | 选择性 |
|------|----------|-------------|--------|
| price | 检索前 | `range` (Lte/Gte) | 高 |
| category | 检索前 | `MatchText` (子串) | 高 |
| series | 检索前 | `MatchText` (子串) | 中 |
| tenant_id | 检索前 | `MatchValue` | 高 |
| 功能关键词 | 检索后 | name 子串匹配 | 低 |
| 科技关键词 | 检索后 | basic_info 子串匹配 | 低 |

## 详细设计

### 1. category_mapping.py（新增）

品类别名映射表，将用户口语映射到数据实际值：

```python
CATEGORY_ALIASES = {
    # 跑步鞋族
    "跑步鞋": ["跑步鞋"],
    "跑鞋": ["跑步鞋"],
    "竞速鞋": ["竞技跑鞋"],
    "马拉松鞋": ["竞技跑鞋"],
    "训练跑鞋": ["竞技跑鞋", "轻质跑鞋"],
    
    # 篮球鞋族
    "篮球鞋": ["篮球鞋", "篮球比赛鞋", "篮球训练鞋", "篮球文化鞋"],
    "实战鞋": ["篮球比赛鞋", "篮球训练鞋"],
    
    # 休闲鞋族
    "休闲鞋": ["运动生活鞋", "经典休闲鞋", "时尚休闲鞋", "潮流鞋"],
    "板鞋": ["经典休闲鞋", "文化休闲鞋"],
    "老爹鞋": ["老爹鞋"],
    "小白鞋": ["小白鞋"],
    "潮流鞋": ["潮流鞋", "时尚休闲鞋"],
    
    # 户外鞋族
    "户外鞋": ["户外鞋", "户外徒步鞋", "户外溯溪鞋"],
    "徒步鞋": ["户外徒步鞋"],
    "溯溪鞋": ["户外溯溪鞋"],
    "越野鞋": ["野外跑鞋"],
    
    # 凉鞋族
    "凉鞋": ["凉鞋", "拖鞋", "托鞋"],
    "拖鞋": ["拖鞋", "托鞋"],
    
    # 专业鞋
    "羽毛球鞋": ["羽毛球比赛鞋", "羽毛球训练鞋"],
    "乒乓球鞋": ["乒乓球训练鞋"],
}

def expand_category(user_category: str) -> list[str]:
    """将用户口语品类展开为数据匹配词列表"""
    return CATEGORY_ALIASES.get(user_category, [user_category])
```

### 2. slot_filling.py 增强

新增提取：系列、功能关键词、品类别名展开。

```python
# 系列关键词
SERIES_KEYWORDS = {
    "驭帅": "驭帅", "利刃": "利刃", "闪击": "闪击",
    "音速": "音速", "全城": "全城", "反伍": "反伍",
    "韦德": "韦德", "飞电": "飞电", "赤兔": "赤兔",
    "绝影": "绝影", "越影": "越影", "烈骏": "烈骏",
    "追风": "追风", "超影": "超影", "星环": "星环",
    "SOFT COOL": "SOFT COOL", "SOFT GO": "SOFT GO",
}

# 功能关键词
FUNCTIONAL_KEYWORDS = {
    "轻量": ["轻量", "轻质", "轻便"],
    "透气": ["透气", "网面", "清凉", "凉爽"],
    "减震": ["减震", "缓震", "回弹"],
    "户外": ["户外", "徒步", "溯溪", "越野"],
    "专业": ["专业", "比赛", "竞技"],
    "休闲": ["休闲", "百搭", "潮流"],
    "防水": ["防水", "拒水"],
}
```

`extract_slots_by_rules()` 增加系列和功能提取。`_enhance_slots()` 增加品类别名展开和功能/科技关键词到 search_keywords。

### 3. get_qdrant_filter() 函数

在 `slot_filling.py` 或 `text_retrieval.py` 中新增：

```python
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchText

def get_qdrant_filter(slots: dict, tenant_id: str = "default") -> Filter:
    conditions = []
    
    if tenant_id and tenant_id != "default":
        conditions.append(FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)))
    
    if slots.get("budget_max"):
        conditions.append(FieldCondition(key="price", range=Lte(value=float(slots["budget_max"]))))
    if slots.get("budget_min"):
        conditions.append(FieldCondition(key="price", range=Gte(value=float(slots["budget_min"]))))
    
    category_aliases = slots.get("category_aliases", [])
    if category_aliases:
        should_conditions = [
            FieldCondition(key="category", match=MatchText(text=kw))
            for kw in category_aliases
        ]
        conditions.append(Filter(should=should_conditions))
    
    if slots.get("series"):
        conditions.append(FieldCondition(key="series", match=MatchText(text=slots["series"])))
    
    return Filter(must=conditions) if conditions else None
```

### 4. text_retrieval.py 改动

`search_by_text()` 新增 `qdrant_filter` 参数，替代当前只用 tenant_id 的逻辑：

```python
def search_by_text(
    text_embedding, top_k=10, score_threshold=0.5,
    category_filter=None, tenant_id=None,
    qdrant_filter=None,  # 新增：完整 Qdrant filter
    collection_name="products",
):
    # 优先使用传入的完整 filter
    if qdrant_filter:
        query_filter = qdrant_filter
    else:
        # 回退到原有逻辑
        filter_conditions = []
        if tenant_id and tenant_id != "default":
            filter_conditions.append(FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)))
        query_filter = Filter(must=filter_conditions) if filter_conditions else None
    ...
```

### 5. search_node 改动

在 `nodes.py:search_node()` 中构建 filter 并传递：

```python
async def search_node(state: AgentState) -> AgentState:
    from app.graph.slot_filling import get_qdrant_filter
    
    slots = state.get("slots", {})
    tenant_id = state.get("tenant_id", "default")
    
    # 构建 Qdrant 过滤条件
    qdrant_filter = get_qdrant_filter(slots, tenant_id)
    
    # 传递 filter 到检索器
    candidates = await retriever.retrieve(
        query=query, text_embedding=text_emb,
        top_k=10, filters={"qdrant_filter": qdrant_filter},
        tenant_id=tenant_id,
    )
    ...
```

### 6. 后置软过滤

在 `postprocessors.py` 新增 `FunctionalFilterPostprocessor`：

```python
class FunctionalFilterPostprocessor(BasePostprocessor):
    """功能关键词后置过滤：检查 name 是否包含用户指定的功能词"""
    
    def process(self, candidates, state):
        slots = state.get("slots", {})
        functional = slots.get("functional", [])
        if not functional:
            return candidates
        
        filtered = []
        for c in candidates:
            name = (c.get("title") or c.get("name", "")).lower()
            if any(kw.lower() in name for kw in functional):
                filtered.append(c)
        
        # 安全回退
        if len(filtered) >= 2:
            return filtered
        return candidates
```

## 安全机制

1. **过滤后结果 < 2 条时回退**：PriceFilter、GenderFilter、FunctionalFilter 都有此逻辑
2. **category_aliases 为空时不过滤**：如果用户没说品类，不做品类过滤
3. **MatchText 无结果时降级**：如果 MatchText 过滤后 0 结果，回退到只用 price 过滤

## 测试计划

### 单元测试
- `test_category_mapping.py`：别名展开正确性
- `test_slot_filling_enhanced.py`：新增字段提取正确性
- `test_qdrant_filter.py`：Filter 构建正确性

### 集成测试
- 用 `rag_eval_dataset.jsonl` 的近似替代用例验证 Recall@K 提升
- 验证同款识别场景 Recall@K 保持 100%

### 回归测试
- 跑完整 `evaluate_performance.py` 确保无回退
