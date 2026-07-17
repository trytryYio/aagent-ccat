"""Prompt 加载器：从 prompts/ 目录读取 .prompt 模板文件并渲染变量。

用法：
    from rag.prompt_loader import load_prompt
    prompt = load_prompt("generate", candidates="...", citations="...")
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# prompts 目录：backend/prompts/ 或项目根 prompts/
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
if not _PROMPTS_DIR.exists():
    _PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def load_prompt(template_name: str, **kwargs) -> str:
    """加载并渲染 prompt 模板。

    Args:
        template_name: 模板名称（不含 .prompt 后缀）
        **kwargs: 模板变量

    Returns:
        渲染后的 prompt 字符串
    """
    path = _PROMPTS_DIR / f"{template_name}.prompt"
    if not path.exists():
        logger.warning("Prompt 模板不存在: %s，使用空字符串", path)
        return ""

    template = path.read_text(encoding="utf-8")
    try:
        return template.format(**kwargs)
    except KeyError as e:
        logger.error("Prompt 模板变量缺失: %s in %s", e, template_name)
        return template
