# Prompt 外置重构（待做事项）

> 创建时间：2026-07-03 | 状态：待实施
>
> 参考：PythonProject16 的 `prompts/` 目录 + `load_prompt()` 模式

---

## 一、现状问题

AgentProject 有 **7 个 Prompt** 全部以 f-string 硬编码在 Python 代码中：

| # | 文件 | 行号 | 函数 | Prompt 用途 |
|---|------|------|------|-----------|
| 1 | `nodes.py` | 24 | `intent_recognition_node` | 意图识别 |
| 2 | `nodes.py` | 99 | `query_rewrite_node` | 多轮对话 query 改写 |
| 3 | `nodes.py` | 281 | `ask_clarify_node` | 澄清问题生成 |
| 4 | `nodes.py` | 354 | `generate_node` | 答案生成 system prompt（最长，30行） |
| 5 | `nodes.py` | 407 | `reflection_node` | 反思自检 |
| 6 | `slot_filling.py` | 100 | `extract_slots_with_llm` | 槽位填充 |
| 7 | `memory.py` | 67 | `build_messages` | 导购角色设定 |

**问题：**
1. **改 Prompt 要改代码**：业务要调优回答风格（比如"加表情""缩短到100字"），必须改 Python 文件 + 走 Git 提交
2. **Prompt 被代码淹没**：打开 `nodes.py` 要在代码和 Prompt 之间反复横跳
3. **无法版本管理**：想同时保留两个版本的 system_prompt 做 A/B 测试，只能复制代码
4. **面试减分**：面试官问"Prompt 怎么管理的"，回答"写死在代码里"不如"外置到文件+模板渲染"

---

## 二、PythonProject16 怎么做的

### 目录结构

```
PythonProject16/
├── prompts/                              ← 所有 Prompt 独立存放
│   ├── answer_out.prompt                  (707 bytes, 答案生成)
│   ├── hyde_prompt.prompt                 (361 bytes, HyDE 检索)
│   ├── image_summary.prompt               (246 bytes, 图片摘要)
│   ├── item_name_recognition.prompt       (438 bytes, 产品名识别)
│   ├── product_recognition_system.prompt   (63 bytes, 系统角色)
│   └── rewritten_query_and_itemnames.prompt (731 bytes, query改写)
└── app/core/load_prompt.py               ← 模板渲染器
```

### `load_prompt.py` 核心代码

```python
from pathlib import Path

def load_prompt(name: str, **kwargs) -> str:
    """加载提示词并渲染变量占位符"""
    prompt_path = PROJECT_ROOT / 'prompts' / f'{name}.prompt'
    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件不存在：{prompt_path}")
    
    raw_prompt = prompt_path.read_text(encoding='utf-8')
    
    if kwargs:
        rendered_prompt = raw_prompt.format(**kwargs)
        logger.debug(f"提示词渲染成功，替换变量：{list(kwargs.keys())}")
        return rendered_prompt
    return raw_prompt
```

### 在业务代码中调用

```python
# 渲染前
prompt = f"""
用户需求：{text}
候选商品：{json.dumps(...)}
请生成一个简短的澄清问题...
"""

# 渲染后（一行搞定）
from app.core.load_prompt import load_prompt
prompt = load_prompt("ask_clarify", text=text, candidates=json.dumps(...))
```

---

## 三、AgentProject 的重构方案

### 3.1 目录规划

```
AgentProject/backend/
├── prompts/                              ← 新建
│   ├── intent_recognition.prompt         (意图识别)
│   ├── query_rewrite.prompt              (query 改写)
│   ├── ask_clarify.prompt                (澄清问题)
│   ├── generate.prompt                   (答案生成 system)
│   ├── reflection.prompt                 (反思自检)
│   ├── slot_filling.prompt               (槽位填充)
│   └── shopping_guide_system.prompt      (导购角色)
└── app/core/load_prompt.py               ← 新建（~20 行）
```

### 3.2 `load_prompt.py` 实现

```python
"""Prompt 模板加载器 — 参考 PythonProject16 的 load_prompt 模式"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目根目录：backend/prompts/
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str, **kwargs) -> str:
    """
    加载 prompts/{name}.prompt 并渲染变量占位符。
    
    用法：
        prompt = load_prompt("ask_clarify", text="轻量化", candidates="[雷霆PRO]")
    
    Args:
        name: Prompt 文件名（不带 .prompt 后缀）
        **kwargs: 要渲染的变量（对应 prompt 文件中的 {variable} 占位符）
    
    Returns:
        渲染后的完整 Prompt 字符串
    """
    prompt_path = _PROMPTS_DIR / f"{name}.prompt"
    
    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件不存在：{prompt_path}")
    
    raw_prompt = prompt_path.read_text(encoding="utf-8")
    
    if kwargs:
        # 使用 Python format 渲染 {{ literal_braces }} 和 {variables}
        rendered = raw_prompt.format(**kwargs)
        logger.debug("[Prompt] 渲染 %s，变量=%s", name, list(kwargs.keys()))
        return rendered
    
    return raw_prompt
```

### 3.3 每个 Prompt 文件内容

#### `prompts/intent_recognition.prompt`

```
根据用户输入判断意图（只返回一个词）：
- find_similar: 用户上传了图片，想找同款或相似商品
- ask_product: 用户询问商品详情、价格、评价等
- compare: 用户要求对比多个商品
- unclear: 无法确定

用户输入：{image_tag} {text}
会话历史条数：{history_count}
```

#### `prompts/query_rewrite.prompt`

```
请根据以下对话历史，把用户最后一句话改写成一条完整、独立的检索 query。
检索 query 应包含历史提到的关键约束（预算、品牌、用途、偏好等），用于召回商品。
只输出改写后的 query，不要解释。

对话历史：
{history_text}

用户当前输入：{text}
```

#### `prompts/ask_clarify.prompt`

```
用户需求：{text}
候选商品：{candidates}
请生成一个简短的澄清问题，帮助进一步缩小推荐范围（预算/用途/偏好）。
只输出问题本身。
```

#### `prompts/generate.prompt`

```
你是电商导购助手。

你必须依据候选商品和知识引用回答，不要编造未提供的商品信息。

候选商品：
{cand_text}

知识引用：
{cit_text}

用户历史偏好：
{pref_text}

用户当前需求：
{slots_text}

对话上下文：
{history_text}

输出要求（严格遵守）：
1. **逐款推荐**：对每款推荐商品，列出商品名称、价格、核心特点和推荐理由
2. **从知识库取数据**：推荐理由必须基于候选商品的"商品介绍"和知识引用中的内容，包括科技特点、材质、脚感、适用场景等
3. **信息不足标注**：如果某款商品描述为空且没有商品介绍，标注"⚠️ 商品信息不足，建议到店试穿"，并说明为何它出现在候选列表（如：标题匹配了你的关键词"xxxx"）
4. **显式引用来源**：如"根据商品介绍，这款鞋采用COMFOAM MAX中底..."或"知识引用提到..."
5. **与用户需求对应**：如果用户提到"脚大""宽脚"，要重点说明这些鞋是否有宽松版型、宽敞鞋楦、不挤脚等特点
6. **禁止寒暄套话**：不要"您好""欢迎继续提问"等
7. **不要编造**：不要推荐候选列表之外的商品，不确定就说"这部分信息我未能确认"
8. **按候选顺序推荐**：必须按照候选商品的排列顺序依次推荐，不要打乱顺序
```

#### `prompts/reflection.prompt`

```
检查以下回答是否符合要求：
1. 是否基于候选商品？候选有 {num_candidates} 个
2. 是否引用了知识？引用有 {num_citations} 条
3. 是否有编造的内容？
4. 是否直接回答了用户问题？

回答：{answer}

如果合格只输出 PASS，否则说明具体问题。
```

#### `prompts/slot_filling.prompt`

```
从以下用户需求中提取结构化信息（只输出 JSON，不要解释）：

用户需求：{text}

提取字段：
- budget_max: 最高预算（数字，没有则为 null）
- budget_min: 最低预算（数字，没有则为 null）
- category: 商品品类（篮球鞋/跑步鞋/羽毛球鞋/休闲鞋等，没有则为 null）
- scenario: 使用场景（室内/室外/比赛/训练，没有则为 null）
- gender: 性别（男/女，没有则为 null）
- foot_type: 脚型（宽脚/窄脚，没有则为 null）
- tech_preferences: 科技偏好列表（缓震/支撑/透气/轻量/耐磨，没有则为 []）

示例输出：
{{"budget_max": 500, "category": "篮球鞋", "tech_preferences": ["缓震", "耐磨"]}}
```

> 注意：JSON 示例中的 `{` 和 `}` 要用 `{{` 和 `}}` 转义，避免被 Python `format()` 误认为变量占位符。

#### `prompts/shopping_guide_system.prompt`

```
你是电商导购助手。
```

### 3.4 业务代码改动

**改动前（`nodes.py`）：**

```python
async def intent_recognition_node(state: AgentState) -> AgentState:
    prompt = f"""
根据用户输入判断意图（只返回一个词）：
...
"""
    response = await llm.ainvoke(prompt)
```

**改动后：**

```python
from app.core.load_prompt import load_prompt

async def intent_recognition_node(state: AgentState) -> AgentState:
    prompt = load_prompt(
        "intent_recognition",
        image_tag="[图片]" if state.get("image_id") else "",
        text=state.get("text") or "",
        history_count=len(state.get("messages", [])),
    )
    response = await llm.ainvoke(prompt)
```

每个节点的改动量：**~3 行**（删掉 f-string，加一行 `load_prompt()`）。

---

## 四、实施优先级

| 优先级 | 内容 | 改动量 | 收益 |
|--------|------|--------|------|
| 🔴 高 | 创建 `load_prompt.py` | ~20 行 | 基础设施，一劳永逸 |
| 🔴 高 | 创建 `prompts/` 目录 + 7 个 `.prompt` 文件 | ~150 行 | Prompt 从代码中剥离 |
| 🟡 中 | 逐个节点替换硬编码 Prompt | 每节点 ~3 行 | 可分批次迁移 |
| 🟢 低 | Prompt 版本管理（git tag 或文件名加版本号） | 约定即可 | 支持 A/B 测试 |

---

## 五、面试问答

> **Q: 你们 Prompt 怎么管理的？**
>
> "我们把所有 Prompt 外置到 `prompts/` 目录下，以 `.prompt` 文件独立存放，与业务代码完全解耦。通过 `load_prompt(name, **kwargs)` 统一加载和模板渲染。好处是：
> 1. **业务人员调优 Prompt 不需要改代码**，只需要改 `.prompt` 文件；
> 2. **版本管理方便**，可以用 Git tag 或文件名加版本号支持 A/B 测试；
> 3. **代码可读性提升**，`nodes.py` 里只剩业务逻辑，Prompt 不再被 f-string 淹没。"

---

## 六、参考链接

- PythonProject16 `load_prompt.py`: `D:\project\PythonProject16\app\core\load_prompt.py`
- PythonProject16 `prompts/` 目录: `D:\project\PythonProject16\prompts\`
- Python 标准库 `str.format()` 转义规则: `{{` = `{`, `}}` = `}`
