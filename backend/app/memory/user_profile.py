"""用户画像数据模型

跨会话持久化的用户画像，包含预算、品牌偏好、品类偏好、风格标签等。
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class UserProfile:
    """用户画像（跨会话持久化）"""

    user_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    budget_range: Optional[tuple] = None              # (min, max)
    preferred_brands: list = field(default_factory=list)
    preferred_categories: list = field(default_factory=list)
    style_tags: list = field(default_factory=list)
    excluded_attributes: list = field(default_factory=list)
    recent_views: list = field(default_factory=list)      # 商品 SKU 列表
    recent_queries: list = field(default_factory=list)    # 查询文本列表
    total_sessions: int = 0
    total_interactions: int = 0

    def to_prompt_context(self) -> str:
        """生成注入 LLM prompt 的上下文文本"""
        parts = []

        if self.budget_range:
            parts.append(f"预算范围：{self.budget_range[0]}-{self.budget_range[1]} 元")

        if self.preferred_brands:
            parts.append(f"偏好品牌：{'，'.join(self.preferred_brands)}")

        if self.preferred_categories:
            parts.append(f"偏好品类：{'，'.join(self.preferred_categories)}")

        if self.style_tags:
            parts.append(f"风格标签：{'，'.join(self.style_tags)}")

        if self.excluded_attributes:
            parts.append(f"排除属性：{'，'.join(self.excluded_attributes)}")

        if not parts:
            return "（暂无用户画像）"

        return "用户画像：\n" + "\n".join(f"- {p}" for p in parts)

    def to_dict(self) -> dict:
        """序列化为 dict（用于 JSON 存储）"""
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        if self.budget_range is not None:
            d["budget_range"] = list(self.budget_range)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        """从 dict 反序列化"""
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        if isinstance(data.get("budget_range"), list):
            data["budget_range"] = tuple(data["budget_range"])
        return cls(**data)

    def merge_from(self, other: "UserProfile") -> None:
        """将另一个画像合并到当前画像（取并集 / 最新值）"""
        if other.budget_range is not None:
            self.budget_range = other.budget_range

        self.preferred_brands = _merge_list(self.preferred_brands, other.preferred_brands)
        self.preferred_categories = _merge_list(self.preferred_categories, other.preferred_categories)
        self.style_tags = _merge_list(self.style_tags, other.style_tags)
        self.excluded_attributes = _merge_list(self.excluded_attributes, other.excluded_attributes)

        self.recent_views = _slide_window(
            self.recent_views + other.recent_views, 20
        )
        self.recent_queries = _slide_window(
            self.recent_queries + other.recent_queries, 20
        )

        self.total_sessions += other.total_sessions
        self.total_interactions += other.total_interactions

        self.updated_at = datetime.now(timezone.utc)


def _merge_list(existing: list, incoming: list) -> list:
    """合并两个列表，去重并保留顺序"""
    seen = set()
    result = []
    for item in existing + incoming:
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _slide_window(items: list, max_size: int = 20) -> list:
    """滑动窗口：只保留最近 max_size 条"""
    if len(items) <= max_size:
        return items
    return items[-max_size:]
