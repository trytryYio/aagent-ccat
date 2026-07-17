import re

PREFERENCE_KEYWORDS = [
    "喜欢",
    "偏好",
    "想要",
    "希望",
    "需要",
    "consider",
    "prefer",
    "want",
    "need",
    "便宜",
    "贵",
    "颜色",
    "size",
    "尺码",
    "品牌",
    "price",
    "性价比",
]


def extract_preferences(history: list[dict]) -> dict:
    prefs: dict[str, str] = {}
    for msg in history:
        text = msg.get("content") or ""
        if any(keyword in text for keyword in PREFERENCE_KEYWORDS):
            parts = re.split(r"[，。！？!?\.]", text)
            for part in parts:
                for keyword in PREFERENCE_KEYWORDS:
                    if keyword in part:
                        prefs[f"{keyword}_preference"] = part.strip()
    return prefs


def format_chat_history(history: list[dict], max_turns: int = 6) -> str:
    recent = history[-max_turns:]
    lines: list[str] = []
    for msg in recent:
        role = msg.get("role", "user")
        content = (msg.get("content") or "").strip()
        if content:
            prefix = "用户" if role == "user" else "助手"
            lines.append(f"{prefix}: {content}")
    return "\n".join(lines)


def _format_preferences(preferences: dict | None) -> str:
    if not preferences:
        return "无"
    lines = [
        f"- {key.replace('_preference', '')}: {value}"
        for key, value in preferences.items()
    ]
    return "\n".join(lines) if lines else "无"


def build_messages(
    history: list[dict],
    text: str,
    candidates_text: str,
    preferences: dict | None = None,
    citations_text: str = "",
) -> list[dict[str, str]]:
    system_parts = [
        "你是电商导购助手。",
        "你必须优先依据候选商品和知识引用回答，不要编造未提供的商品信息。",
        "如果信息不足，应该调用澄清工具向用户追问。",
        "",
        "候选商品：",
        candidates_text or "无",
        "",
        "知识引用：",
        citations_text or "无",
        "",
        "用户偏好：",
        _format_preferences(preferences),
        "",
        "输出要求：",
        "- 使用中文回答。",
        "- 推荐理由要尽量引用候选商品属性或知识引用。",
        "- 不要推荐候选列表之外的商品。",
    ]

    messages = [{"role": "system", "content": "\n".join(system_parts)}]

    chat_history = format_chat_history(history)
    if chat_history:
        messages.append(
            {
                "role": "user",
                "content": f"对话历史：\n{chat_history}\n\n当前问题：{text}",
            }
        )
    else:
        messages.append({"role": "user", "content": text or "推荐类似的产品"})

    return messages
