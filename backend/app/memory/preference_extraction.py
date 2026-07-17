"""偏好提取器：从用户交互中提取偏好信息（规则版，MVP）

关键词字典：
- 品牌：飞电/韦德 → 李宁，Nike → Nike，Adidas → Adidas 等
- 风格：透气 → 透气，轻便 → 轻量 等
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 品牌关键词映射（关键词 → 标准品牌名）
BRAND_KEYWORDS = {
    # 李宁系列
    "飞电": "李宁", "韦德": "李宁", "驭帅": "李宁",
    "利刃": "李宁", "闪击": "李宁", "音速": "李宁",
    "全城": "李宁", "反伍": "李宁", "赤兔": "李宁",
    "绝影": "李宁", "越影": "李宁", "烈骏": "李宁",
    "追风": "李宁", "超影": "李宁", "星环": "李宁",
    "李宁": "李宁", "lining": "李宁", "Lining": "李宁",
    # 国际品牌
    "Nike": "Nike", "nike": "Nike", "NIKE": "Nike",
    "耐克": "Nike", "Adidas": "Adidas", "adidas": "Adidas",
    "阿迪": "Adidas", "Jordan": "Jordan", "jordan": "Jordan",
    "匡威": "Converse", "Converse": "Converse",
    "New Balance": "New Balance", "new balance": "New Balance",
    "NB": "New Balance", "nb": "New Balance",
    "安踏": "Anta", "Anta": "Anta", "anta": "Anta",
    "匹克": "Peak", "Peak": "Peak",
    "361": "361度", "361度": "361度",
    "特步": "Xtep", "Xtep": "Xtep",
    "鸿星尔克": "Erke", "Erke": "Erke",
}

# 风格关键词映射（关键词 → 标准风格标签）
STYLE_KEYWORDS = {
    "透气": "透气", "网面": "透气", "清凉": "透气",
    "轻便": "轻量", "轻量": "轻量", "轻质": "轻量",
    "轻": "轻量",
    "减震": "缓震", "缓震": "缓震", "软弹": "缓震",
    "回弹": "缓震", "踩屎感": "缓震",
    "稳定": "支撑", "支撑": "支撑", "防侧翻": "支撑",
    "耐磨": "耐磨", "橡胶底": "耐磨",
    "防水": "防水", "拒水": "防水",
    "休闲": "休闲", "百搭": "休闲", "潮流": "休闲",
    "专业": "专业", "比赛": "专业", "竞技": "专业",
    "户外": "户外", "徒步": "户外", "越野": "户外",
}

# 品类关键词
CATEGORY_KEYWORDS = {
    "篮球鞋": ["篮球", "球场", "实战", "cba", "nba"],
    "跑步鞋": ["跑步", "慢跑", "马拉松", "run", "竞速"],
    "羽毛球鞋": ["羽毛球", "杀球", "接杀"],
    "训练鞋": ["训练", "健身", "gym"],
    "休闲鞋": ["休闲", "日常", "百搭", "潮流"],
    "凉鞋": ["凉鞋", "拖鞋", "夏季"],
    "户外鞋": ["户外", "徒步", "溯溪", "越野"],
}

# 排除关键词
EXCLUDE_PATTERNS = [
    r"不要\s*(.+?)(?:[，。！？]|$)",
    r"别\s*(.+?)(?:[，。！？]|$)",
    r"不喜欢\s*(.+?)(?:[，。！？]|$)",
    r"排除\s*(.+?)(?:[，。！？]|$)",
    r"除了\s*(.+?)(?:[，。！？]|$)",
]


def extract_budget(text: str) -> Optional[tuple]:
    """从文本中提取预算范围，返回 (min, max) 或 None"""
    # 范围格式：300-500、300 到 500
    range_match = re.search(r"(\d+)\s*(?:-|~|到|至)\s*(\d+)", text)
    if range_match:
        return (int(range_match.group(1)), int(range_match.group(2)))

    # 上限格式：500元以内、不超过500、预算500
    max_match = re.search(r"(?:不超过|以内|以下|预算|价格)\s*(?:大概|大约|是)?\s*(\d+)", text)
    if max_match:
        return (0, int(max_match.group(1)))

    # 纯数字 + 元
    yuan_match = re.search(r"(\d+)\s*元", text)
    if yuan_match:
        return (0, int(yuan_match.group(1)))

    return None


def extract_brands(text: str) -> list:
    """从文本中提取品牌偏好"""
    brands = set()
    for keyword, standard_name in BRAND_KEYWORDS.items():
        if keyword in text:
            brands.add(standard_name)
    return list(brands)


def extract_style_tags(text: str) -> list:
    """从文本中提取风格标签"""
    tags = set()
    for keyword, standard_tag in STYLE_KEYWORDS.items():
        if keyword in text:
            tags.add(standard_tag)
    return list(tags)


def extract_categories(text: str) -> list:
    """从文本中提取品类偏好"""
    categories = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text.lower() for kw in keywords):
            categories.append(category)
    return categories


def extract_excluded(text: str) -> list:
    """从文本中提取排除属性"""
    excluded = []
    for pattern in EXCLUDE_PATTERNS:
        matches = re.findall(pattern, text)
        excluded.extend(matches)
    return excluded


def extract_all(text: str) -> dict:
    """从一段用户文本中提取所有偏好信息"""
    result = {}

    budget = extract_budget(text)
    if budget is not None:
        result["budget_range"] = budget

    brands = extract_brands(text)
    if brands:
        result["preferred_brands"] = brands

    categories = extract_categories(text)
    if categories:
        result["preferred_categories"] = categories

    style_tags = extract_style_tags(text)
    if style_tags:
        result["style_tags"] = style_tags

    excluded = extract_excluded(text)
    if excluded:
        result["excluded_attributes"] = excluded

    logger.debug("Extracted preferences from text: %s", result)
    return result
