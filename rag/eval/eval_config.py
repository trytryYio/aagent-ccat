"""评估配置入口

统一配置 API 地址和模型名称，方便切换不同的大模型。
所有大模型配置都从 backend/.env 读取，修改 .env 即可全局生效。
"""
import os
import sys
from pathlib import Path

# 确保 backend 在 sys.path 中，以便导入 app.config
_project_root = Path(__file__).parent.parent.parent
_backend_dir = _project_root / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# 尝试从 backend/app/config.py 导入 Settings（避免重复逻辑）
try:
    from app.config import settings as _settings
    _USE_PYDANTIC_SETTINGS = True
except (ImportError, AttributeError):
    # 如果导入失败，回退到手动加载 .env
    _USE_PYDANTIC_SETTINGS = False
    _env_path = _backend_dir / ".env"
    if _env_path.exists():
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())


class EvalConfig:
    """评估配置类

    统一从 backend/.env 读取配置，修改 .env 即可全局生效。
    优先使用 backend/app/config.py 的 Settings 类，避免重复逻辑。
    """

    @classmethod
    def get_config(cls) -> dict:
        """获取当前配置"""
        if _USE_PYDANTIC_SETTINGS:
            return {
                "api_key": _settings.llm_api_key,
                "base_url": _settings.llm_base_url,
                "model": _settings.llm_model,
            }
        else:
            return {
                "api_key": os.environ.get("LLM_API_KEY", ""),
                "base_url": os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                "model": os.environ.get("LLM_MODEL", "qwen-plus"),
            }

    @classmethod
    def get_fallback_config(cls) -> dict | None:
        """获取 fallback 配置（如果配置了的话）"""
        if _USE_PYDANTIC_SETTINGS:
            fallback_model = _settings.llm_fallback_model
            if not fallback_model:
                return None
            return {
                "api_key": _settings.llm_fallback_api_key or _settings.llm_api_key,
                "base_url": _settings.llm_fallback_base_url or _settings.llm_base_url,
                "model": fallback_model,
            }
        else:
            fallback_model = os.environ.get("LLM_FALLBACK_MODEL", "")
            if not fallback_model:
                return None
            return {
                "api_key": os.environ.get("LLM_FALLBACK_API_KEY", "") or os.environ.get("LLM_API_KEY", ""),
                "base_url": os.environ.get("LLM_FALLBACK_BASE_URL", "") or os.environ.get("LLM_BASE_URL", ""),
                "model": fallback_model,
            }
