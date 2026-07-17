"""槽位填充：从用户 query 中提取结构化信息（预算/品类/偏好）。

用于：
1. 结构化过滤（price/category filter）
2. 动态澄清问题生成
3. 推荐解释（"因为你提到预算 500 元..."）
"""

import re
import json
import logging
from typing import Optional
from app.graph.llm import get_llm

logger = logging.getLogger(__name__)


# 品类关键词映射
CATEGORY_KEYWORDS = {
    "篮球鞋": ["篮球", "球场", "实战", "cba", "nba"],
    "跑步鞋": ["跑步", "慢跑", "马拉松", "run", "竞速"],
    "羽毛球鞋": ["羽毛球", "球场", "杀球", "接杀"],
    "乒乓球鞋": ["乒乓球", "桌球"],
    "训练鞋": ["训练", "健身", "gym"],
    "休闲鞋": ["休闲", "日常", "百搭", "潮流"],
    "凉鞋": ["凉鞋", "拖鞋", "夏季"],
    "户外鞋": ["户外", "徒步", "溯溪", "越野"],
}

# 场景关键词
SCENARIO_KEYWORDS = {
    "室内": ["室内", "健身房", "球场"],
    "室外": ["室外", "水泥地", "塑胶"],
    "比赛": ["比赛", "正式", "竞技"],
    "训练": ["训练", "日常", "练习"],
}

# 系列关键词（从商品名称中提取）
SERIES_KEYWORDS = {
    "驭帅": "驭帅", "利刃": "利刃", "闪击": "闪击",
    "音速": "音速", "全城": "全城", "反伍": "反伍",
    "韦德": "韦德", "飞电": "飞电", "赤兔": "赤兔",
    "绝影": "绝影", "越影": "越影", "烈骏": "烈骏",
    "追风": "追风", "超影": "超影", "星环": "星环",
    "SOFT COOL": "SOFT COOL", "SOFT GO": "SOFT GO",
    "COMMON": "COMMON", "惟吾": "惟吾",
}

# 功能关键词（用于后置软过滤）
FUNCTIONAL_KEYWORDS = {
    "轻量": ["轻量", "轻质", "轻便"],
    "透气": ["透气", "网面", "清凉", "凉爽"],
    "减震": ["减震", "缓震", "回弹"],
    "户外": ["户外", "徒步", "溯溪", "越野"],
    "专业": ["专业", "比赛", "竞技"],
    "休闲": ["休闲", "百搭", "潮流"],
    "防水": ["防水", "拒水"],
}


def extract_slots_by_rules(text: str) -> dict:
    """基于规则的槽位提取（快速、确定性高）。"""
    slots = {}

    # 1. 预算提取（支持多种格式）
    budget_patterns = [
        r"(\d+)\s*元?(?:以内|以下|左右)?",  # 500元以内
        r"(?:预算|价格)\s*(?:在|是|大概)?\s*(\d+)",  # 预算500
        r"不超过\s*(\d+)",  # 不超过500
        r"(\d+)\s*-\s*(\d+)",  # 300-500
    ]
    for pattern in budget_patterns:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                slots["budget_min"] = int(groups[0])
                slots["budget_max"] = int(groups[1])
            else:
                slots["budget_max"] = int(groups[0])
            break

    # 2. 品类提取
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text.lower() for kw in keywords):
            slots["category"] = category
            break

    # 3. 场景提取
    for scenario, keywords in SCENARIO_KEYWORDS.items():
        if any(kw in text.lower() for kw in keywords):
            slots["scenario"] = scenario
            break

    # 4. 性别提取
    if any(kw in text for kw in ["男", "男士", "男生"]):
        slots["gender"] = "男"
    elif any(kw in text for kw in ["女", "女士", "女生"]):
        slots["gender"] = "女"

    # 5. 脚型提取
    if any(kw in text for kw in ["宽脚", "脚宽", "宽楦", "脚大", "大脚", "脚胖", "脚肥", "脚背高"]):
        slots["foot_type"] = "宽脚"
    elif any(kw in text for kw in ["窄脚", "脚窄"]):
        slots["foot_type"] = "窄脚"

    # 6. 科技偏好提取
    tech_keywords = {
        "缓震": ["缓震", "减震", "软弹", "踩屎感"],
        "支撑": ["支撑", "稳定", "防侧翻"],
        "透气": ["透气", "网面", "凉爽"],
        "轻量": ["轻量", "轻便", "轻"],
        "耐磨": ["耐磨", "橡胶底"],
    }
    for tech, keywords in tech_keywords.items():
        if any(kw in text for kw in keywords):
            slots.setdefault("tech_preferences", []).append(tech)

    # 7. 系列提取
    for series_name, series_val in SERIES_KEYWORDS.items():
        if series_name.lower() in text.lower():
            slots["series"] = series_val
            break

    # 8. 功能关键词提取（用于后置软过滤）
    functional = []
    for func, keywords in FUNCTIONAL_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            functional.append(func)
    if functional:
        slots["functional"] = functional

    return slots


async def extract_slots_with_llm(text: str) -> dict:
    """基于 LLM 的槽位提取（兜底，处理复杂表达）。"""
    prompt = f"""从以下用户需求中提取结构化信息（只输出 JSON，不要解释）：

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
"""
    try:
        llm = get_llm(temperature=0.0)
        response = await llm.ainvoke(prompt)
        content = response.content.strip()
        # 提取 JSON
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            import json
            slots = json.loads(match.group())
            state_usage = llm.last_usage.to_dict() if llm.last_usage else {}
            return slots, state_usage
        return {}, {}
    except Exception as e:
        logger.warning(f"LLM 槽位提取失败: {e}")
        return {}, {}


async def extract_slots(text: str) -> tuple[dict, dict]:
    """槽位提取：先规则，后 LLM 兜底。"""
    # 1. 规则提取（快速）
    slots = extract_slots_by_rules(text)

    # 2. 如果关键字段缺失，用 LLM 兜底
    if not slots.get("category") and not slots.get("budget_max"):
        llm_slots, usage = await extract_slots_with_llm(text)
        # 合并（LLM 结果优先）
        slots.update({k: v for k, v in llm_slots.items() if v is not None})
        slots = _enhance_slots(slots)
        return slots, usage

    slots = _enhance_slots(slots)
    return slots, {}


def _enhance_slots(slots: dict) -> dict:
    """根据已提取的槽位，补充检索增强关键词到 slots['search_keywords']"""
    from rag.category_mapping import expand_category

    keywords = []

    # 品类别名展开（用于 Qdrant MatchText 过滤）
    if slots.get("category"):
        slots["category_aliases"] = expand_category(slots["category"])

    if slots.get("foot_type") == "宽脚":
        keywords.extend(["宽脚", "宽楦", "大码", "宽敞", "不挤脚", "宽松舒适", "宽版"])

    if slots.get("tech_preferences"):
        for tech in slots["tech_preferences"]:
            if tech == "缓震":
                keywords.extend(["缓震", "减震", "软弹"])
            elif tech == "支撑":
                keywords.extend(["支撑", "稳定", "防侧翻"])
            elif tech == "透气":
                keywords.extend(["透气", "网面", "干爽"])
            elif tech == "轻量":
                keywords.extend(["轻量", "轻便"])
            elif tech == "耐磨":
                keywords.extend(["耐磨", "橡胶底"])

    # 功能关键词也加入 search_keywords 增强检索
    if slots.get("functional"):
        for func in slots["functional"]:
            func_kw = FUNCTIONAL_KEYWORDS.get(func, [])
            keywords.extend(func_kw)

    if keywords:
        slots["search_keywords"] = " ".join(keywords)

    return slots


def format_slots_for_prompt(slots: dict) -> str:
    """将槽位格式化为 prompt 片段。"""
    parts = []
    if slots.get("budget_max"):
        if slots.get("budget_min"):
            parts.append(f"预算 {slots['budget_min']}-{slots['budget_max']} 元")
        else:
            parts.append(f"预算 {slots['budget_max']} 元以内")
    if slots.get("category"):
        parts.append(f"品类：{slots['category']}")
    if slots.get("scenario"):
        parts.append(f"场景：{slots['scenario']}")
    if slots.get("gender"):
        parts.append(f"性别：{slots['gender']}")
    if slots.get("foot_type"):
        parts.append(f"脚型：{slots['foot_type']}")
    if slots.get("tech_preferences"):
        parts.append(f"偏好：{', '.join(slots['tech_preferences'])}")

    return "；".join(parts) if parts else "无明确偏好"


def get_missing_slots(slots: dict) -> list[str]:
    """获取缺失的关键槽位（用于生成澄清问题）。"""
    missing = []
    if not slots.get("category"):
        missing.append("品类")
    if not slots.get("budget_max"):
        missing.append("预算")
    if not slots.get("scenario"):
        missing.append("使用场景")
    return missing


def get_qdrant_filter(slots: dict, tenant_id: str = "default"):
    """从 slots 构建 Qdrant 检索前过滤条件。

    当前支持的硬过滤维度：
    - tenant_id（KEYWORD 索引）

    price 存储为字符串，Range 过滤器无效，留给后置软过滤。
    category/series 需要 TEXT 索引，留给后置软过滤。

    Returns:
        qdrant_client.models.Filter 对象，或 None（无过滤条件时）
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    conditions = []

    # tenant_id（必须）
    if tenant_id and tenant_id != "default":
        conditions.append(
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
        )

    # price/category/series 留给后置软过滤

    if not conditions:
        return None
    return Filter(must=conditions)
