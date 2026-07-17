"""后处理器链式架构：检索结果 → Rerank → 过滤 → 排序 → 截断 → 置信度门控

每个 Postprocessor 职责单一，可独立测试，通过 PostprocessorPipeline 链式组合。
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class BasePostprocessor(ABC):
    """后处理器基类"""

    @abstractmethod
    def process(self, candidates: list[dict], state: dict) -> list[dict]:
        """处理候选列表，返回处理后的列表。可修改 state（如设置 need_clarify）。"""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


class ContentQualitySortPostprocessor(BasePostprocessor):
    """候选排序：检索相关性优先，内容质量仅作为同分候选的次级排序。"""

    def process(self, candidates: list[dict], state: dict) -> list[dict]:
        def _content_score(c: dict) -> float:
            desc = (c.get("description") or "").strip()
            intro = (c.get("introduction") or "").strip()
            detail = (c.get("detail_images") or "").strip()
            base = 1.0 if (desc or intro) else 0.0
            text_len = len(desc) + len(intro)
            bonus = min(text_len / 500, 0.5)
            img_bonus = 0.3 if detail else 0.0
            return base + bonus + img_bonus

        candidates.sort(
            key=lambda c: (
                (c.get("metadata") or {}).get("confidence_score", c.get("score", 0)),
                c.get("score", 0),
                _content_score(c),
            ),
            reverse=True,
        )
        return candidates


class PriceFilterPostprocessor(BasePostprocessor):
    """价格过滤：根据 slots 中的 budget_min/budget_max 过滤候选商品。

    安全阈值：过滤后结果 < 2 条时保留原始结果，避免完全无结果。
    """

    def process(self, candidates: list[dict], state: dict) -> list[dict]:
        slots = state.get("slots", {})
        budget_max = slots.get("budget_max")
        budget_min = slots.get("budget_min")
        if not budget_max and not budget_min:
            return candidates

        filtered = []
        for c in candidates:
            price = c.get("price")
            if price is None:
                continue
            if budget_min and price < budget_min:
                continue
            if budget_max and price > budget_max:
                continue
            filtered.append(c)

        if len(filtered) >= 2 or not candidates:
            logger.info("[PriceFilter] %d → %d candidates (budget=%s-%s)",
                        len(candidates), len(filtered), budget_min, budget_max)
            return filtered
        else:
            logger.warning("[PriceFilter] 过滤后结果太少(%d)，保留原始 %d 条",
                           len(filtered), len(candidates))
            return candidates


class GenderFilterPostprocessor(BasePostprocessor):
    """性别过滤：根据 slots 中的 gender 过滤候选商品。

    匹配规则：商品 title/category 中包含性别关键词。
    """

    _GENDER_KEYWORDS = {
        "男": ["男", "men", "male", "MEN"],
        "女": ["女", "women", "female", "WOMEN"],
        "中性": ["中性", "unisex", "通用"],
    }

    def process(self, candidates: list[dict], state: dict) -> list[dict]:
        slots = state.get("slots", {})
        gender = slots.get("gender")
        if not gender:
            return candidates

        keywords = self._GENDER_KEYWORDS.get(gender, [])
        if not keywords:
            return candidates

        def _matches(c: dict) -> bool:
            text = f"{c.get('title', '')} {c.get('category', '')}".lower()
            return any(kw.lower() in text for kw in keywords)

        filtered = [c for c in candidates if _matches(c)]
        if len(filtered) >= 2:
            logger.info("[GenderFilter] %d → %d candidates (gender=%s)",
                        len(candidates), len(filtered), gender)
            return filtered
        else:
            logger.info("[GenderFilter] 性别过滤后太少(%d)，保留原始结果", len(filtered))
            return candidates


class DynamicTopKPostprocessor(BasePostprocessor):
    """动态 TopK 截断：检测相邻分数的断崖式下降，自动截断不相关的结果。

    截断条件（满足任一即截断）：
    - 相对下降 ≥ 25%（score[i] - score[i+1]）/ score[i] ≥ 0.25
    - 绝对下降 ≥ 0.15
    保底：至少保留 3 条结果。
    """

    def __init__(self, min_keep: int = 3, relative_threshold: float = 0.25,
                 absolute_threshold: float = 0.15):
        self.min_keep = min_keep
        self.relative_threshold = relative_threshold
        self.absolute_threshold = absolute_threshold

    def process(self, candidates: list[dict], state: dict) -> list[dict]:
        if len(candidates) <= self.min_keep:
            return candidates

        cut_idx = len(candidates)
        for i in range(self.min_keep - 1, len(candidates) - 1):
            s_curr = (candidates[i].get("metadata") or {}).get(
                "confidence_score", candidates[i].get("score", 0)
            )
            s_next = (candidates[i + 1].get("metadata") or {}).get(
                "confidence_score", candidates[i + 1].get("score", 0)
            )
            if s_curr <= 0:
                continue

            relative_drop = (s_curr - s_next) / s_curr
            absolute_drop = s_curr - s_next

            if relative_drop >= self.relative_threshold or absolute_drop >= self.absolute_threshold:
                cut_idx = i + 1
                logger.info("[DynamicTopK] 断崖检测: position %d, score %.3f → %.3f "
                            "(drop: %.1f%%, %.3f), 截断为 %d 条",
                            i, s_curr, s_next, relative_drop * 100, absolute_drop, cut_idx)
                break

        return candidates[:cut_idx]


class FunctionalFilterPostprocessor(BasePostprocessor):
    """功能关键词后置过滤：检查 name/title 是否包含用户指定的功能词。

    用于低选择性维度（轻量、透气、减震等），这些维度不适合做 Qdrant 硬过滤，
    但可以在检索后进一步筛选。
    """

    def process(self, candidates: list[dict], state: dict) -> list[dict]:
        slots = state.get("slots", {})
        functional = slots.get("functional", [])
        if not functional:
            return candidates

        filtered = []
        for c in candidates:
            name = (c.get("title") or c.get("name", "")).lower()
            if any(kw.lower() in name for kw in functional):
                filtered.append(c)

        # 安全回退：过滤后结果太少时保留原始列表
        if len(filtered) >= 2:
            logger.info("[FunctionalFilter] %d → %d candidates (functional=%s)",
                        len(candidates), len(filtered), functional)
            return filtered
        else:
            logger.info("[FunctionalFilter] 过滤后结果太少(%d)，保留原始 %d 条",
                        len(filtered), len(candidates))
            return candidates


class ConfidenceGatePostprocessor(BasePostprocessor):
    """置信度门控：根据检索结果质量 + 槽位完整度决定是否触发澄清。

    双重触发条件（满足任一即澄清）：
    1. top1 score < threshold → 低置信度澄清
    2. 存在关键偏好槽位（foot_type/budget/tech_preferences）→ 友好确认
    """

    # 这些槽位存在时说明用户有明确偏好，值得二次确认以精准推荐
    _KEY_PREFERENCE_SLOTS = {"foot_type", "tech_preferences", "series_preference"}

    def __init__(self, threshold: float = 0.55):
        self.threshold = threshold

    def process(self, candidates: list[dict], state: dict) -> list[dict]:
        # 用户已回答澄清，跳过
        if state.get("clarify_answered"):
            return candidates

        slots = state.get("slots", {})

        if not candidates:
            state["need_clarify"] = True
            state["clarify_question"] = (
                "为了给你推荐更准确的商品，能先告诉我你大概想找什么类型的鞋吗？"
                "比如比赛用、日常训练还是休闲款？"
            )
            return candidates

        metadata = candidates[0].get("metadata") or {}
        top_score = metadata.get("confidence_score", candidates[0].get("score", 0))

        # 条件 1：低置信度
        if top_score < self.threshold:
            state["need_clarify"] = True
            state["clarify_question"] = (
                "为了给你推荐更合适的商品，想多了解一下你的需求："
                "请问你的预算大概在什么范围？平时打球是什么风格（进攻型/防守型）？"
                "脚型偏宽还是偏窄？"
            )
            return candidates

        # 条件 2：有明确偏好槽位 → 友好确认（即使分数够高）
        filled_key_slots = self._KEY_PREFERENCE_SLOTS & set(slots.keys())
        if filled_key_slots:
            # 把槽位翻译成人话
            slot_hints = []
            if "foot_type" in slots:
                slot_hints.append(f"你提到脚型偏{slots['foot_type']}")
            if "tech_preferences" in slots and slots["tech_preferences"]:
                slot_hints.append(f"偏好{'+'.join(slots['tech_preferences'])}科技")
            if "series_preference" in slots:
                slot_hints.append(f"对{slots['series_preference']}系列感兴趣")

            if slot_hints:
                state["need_clarify"] = True
                hint_text = "，".join(slot_hints)
                state["clarify_question"] = (
                    f"为了给你推荐更准确的商品，想跟你确认一下细节：\n"
                    f"{hint_text}，那你的预算大概在什么范围？"
                    f"主要是日常训练还是比赛穿？"
                )

        return candidates


class PostprocessorPipeline:
    """后处理器管线：按注册顺序依次执行。

    用法：
        pipeline = PostprocessorPipeline()
        pipeline.add(PriceFilterPostprocessor())
        pipeline.add(GenderFilterPostprocessor())
        pipeline.add(ContentQualitySortPostprocessor())
        pipeline.add(DynamicTopKPostprocessor())
        pipeline.add(ConfidenceGatePostprocessor())
        candidates = pipeline.run(candidates, state)
    """

    def __init__(self):
        self._processors: list[BasePostprocessor] = []

    def add(self, processor: BasePostprocessor) -> "PostprocessorPipeline":
        self._processors.append(processor)
        return self

    def run(self, candidates: list[dict], state: dict) -> list[dict]:
        for proc in self._processors:
            before = len(candidates)
            candidates = proc.process(candidates, state)
            after = len(candidates)
            if before != after:
                logger.debug("[Pipeline] %s: %d → %d", proc.name, before, after)
        return candidates


def build_default_pipeline() -> PostprocessorPipeline:
    """构建默认后处理器管线。"""
    return (
        PostprocessorPipeline()
        .add(PriceFilterPostprocessor())
        .add(GenderFilterPostprocessor())
        .add(FunctionalFilterPostprocessor())
        .add(ContentQualitySortPostprocessor())
        .add(DynamicTopKPostprocessor())
    )


def build_clarify_gate() -> ConfidenceGatePostprocessor:
    """构建置信度门控（单独用于 decide_clarify_node）。"""
    return ConfidenceGatePostprocessor(threshold=0.55)
