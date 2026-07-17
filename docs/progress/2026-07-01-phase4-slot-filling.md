# 2026-07-01 晚间冲刺 — 阶段四实施（部分）

> 时间：2026-07-01 19:00 - 20:00  
> 目标：完成阶段四（产品体验提升）的部分功能

---

## 已完成

### ✅ 4.1 Query 理解与改写 — 槽位填充

**新增文件**：
- `backend/app/graph/slot_filling.py` — 槽位填充模块

**功能**：
1. **规则提取**（快速、确定性高）：
   - 预算提取（支持多种格式：500元以内、预算500、300-500）
   - 品类识别（篮球鞋/跑步鞋/羽毛球鞋/休闲鞋等）
   - 场景识别（室内/室外/比赛/训练）
   - 性别/脚型/科技偏好提取

2. **LLM 兜底**（处理复杂表达）：
   - 当规则提取失败时，调用 LLM 提取结构化信息
   - 输出 JSON 格式，包含 budget/category/scenario/gender/foot_type/tech_preferences

**集成到 Agent 流程**：
- 新增 `slot_filling_node` 节点
- 更新 `plan_node` 逻辑，在有文本输入时执行槽位填充
- 更新 `AgentState`，添加 `slots` 字段
- 更新 `graph.py`，注册节点并调整边

**流程**：
```
plan → query_rewrite（可选）→ slot_filling → embed_text → search → ...
plan → embed_image → slot_filling → embed_text → search → ...
```

**后续用途**：
- 结构化过滤（price/category filter）— 待实现
- 动态澄清问题生成 — 待实现
- 推荐解释（"因为你提到预算 500 元..."）— 待实现

---

## 待完成（阶段四剩余）

### ⏸️ 4.2 澄清门控优化（多因子门控）
- 当前：单一阈值（top_score < 0.6）
- 目标：多因子门控（top1 score + top1-top2 gap + 槽位完整度）
- 动态生成澄清问题（基于缺失槽位）

### ⏸️ 4.3 结构化过滤（price/category filter）
- 在 Qdrant 检索时加入 price 范围 filter
- 在 Qdrant 检索时加入 category filter
- 使用槽位填充的结果

### ⏸️ 4.4 答案生成增强
- 显式引用商品名称和 citation 来源
- 增加"不确定"表达
- 加入推荐解释

### ⏸️ 4.5 前端/移动端体验
- 优先级低，面试前不建议做

---

## 技术决策

### 为什么槽位填充用"规则 + LLM 兜底"？
- **规则提取**：快速（<10ms）、确定性高、不消耗 token
- **LLM 兜底**：处理复杂表达（"我想找一双适合宽脚的篮球鞋，预算不超过 600"）
- **成本平衡**：只在规则提取失败时调用 LLM，节省成本

### 为什么 slot_filling 在 query_rewrite 之后？
- 改写后的 query 更完整（包含历史上下文）
- 例如：历史提到"300 以内"，本轮问"有蓝色的吗"
  - 原始 query："有蓝色的吗"
  - 改写后："300 以内蓝色羽毛球鞋"
  - 槽位填充能提取到 budget_max=300, category=篮球鞋

---

## Git 提交计划

```
feat: 阶段四（产品体验提升）— 槽位填充模块

- 新增 slot_filling.py：规则提取 + LLM 兜底
- 集成到 Agent 流程：slot_filling_node
- 更新 AgentState：添加 slots 字段
- 用途：结构化过滤、动态澄清、推荐解释
```

---

## 下一步

1. 实现结构化过滤（4.3）
2. 实现多因子澄清门控（4.2）
3. 实现答案生成增强（4.4）
4. 测试完整流程
