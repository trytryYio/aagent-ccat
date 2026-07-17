"""LLM 工厂 + 调用治理层。

治理四件套：
- 重试：tenacity 指数退避，覆盖偶发网络/API 失败
- 熔断：circuitbreaker，失败率过高时快速失败，防止重试风暴
- fallback：主模型 DeepSeek 失败时切备用模型
- 限流：令牌桶，避免短时请求过多打爆额度

同时统计每次调用的 token usage，供 FinalEvent.usage 回填。
"""

import asyncio
import logging
import os
import time
from typing import Any, Optional

from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from circuitbreaker import circuit

try:
    from app.config import settings
except Exception:  # 方便单独测试
    settings = None

logger = logging.getLogger(__name__)

# 按 (model, temperature, streaming) 缓存原始 LLM 实例
_llm_cache: dict[tuple, ChatOpenAI] = {}

# 统计与限流状态（进程内，后续可迁 Redis）
_token_bucket: dict[str, dict[str, Any]] = {}


class UsageRecord:
    """单次 LLM 调用的 token 使用记录。"""

    def __init__(self):
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.model: str = ""

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "model": self.model,
        }


def _get_llm_raw(
    model: str,
    temperature: float,
    streaming: bool,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> ChatOpenAI:
    """创建原始 LLM 实例（不包装治理）。

    优先级：函数参数 > settings 对象 > 环境变量
    """
    key = (model, temperature, streaming, max_tokens, api_key, base_url)
    if key not in _llm_cache:
        # 从 settings 获取默认值（settings 自己从 .env 读取）
        if settings is not None:
            _api_key = api_key or settings.llm_api_key
            _base_url = base_url or settings.llm_base_url
            _max_tokens = max_tokens or settings.llm_max_tokens
        else:
            # 独立测试模式：直接从环境变量读取
            _api_key = api_key or os.environ.get("LLM_API_KEY", "")
            _base_url = base_url or os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            _max_tokens = max_tokens or int(os.environ.get("LLM_MAX_TOKENS", "2048"))

        _llm_cache[key] = ChatOpenAI(
            model=model,
            api_key=_api_key,
            base_url=_base_url,
            temperature=temperature,
            max_tokens=_max_tokens,
            timeout=settings.llm_timeout if settings else 60,
            streaming=streaming,
        )
    return _llm_cache[key]


def _extract_usage(response, model: str) -> UsageRecord:
    """从 LangChain 响应中提取 token usage。"""
    usage = UsageRecord()
    usage.model = model

    # 1. 尝试 usage_metadata（LangChain 标准）
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        um = response.usage_metadata
        usage.prompt_tokens = int(um.get("input_tokens", 0) or um.get("prompt_tokens", 0))
        usage.completion_tokens = int(um.get("output_tokens", 0) or um.get("completion_tokens", 0))
        usage.total_tokens = int(um.get("total_tokens", 0) or (usage.prompt_tokens + usage.completion_tokens))
        return usage

    # 2. 尝试 response_metadata
    meta = getattr(response, "response_metadata", {}) or {}
    token_usage = meta.get("token_usage") or meta.get("usage") or {}
    if token_usage:
        usage.prompt_tokens = int(token_usage.get("prompt_tokens", 0) or token_usage.get("input_tokens", 0))
        usage.completion_tokens = int(token_usage.get("completion_tokens", 0) or token_usage.get("output_tokens", 0))
        usage.total_tokens = int(token_usage.get("total_tokens", 0) or (usage.prompt_tokens + usage.completion_tokens))
    return usage


# ---- 令牌桶限流 ----
def _acquire_rate_limit(key: str, max_rate: float = 10.0, window_seconds: float = 1.0) -> bool:
    """简单令牌桶限流。返回 True 表示允许通过。"""
    now = time.time()
    bucket = _token_bucket.get(key)
    if bucket is None:
        bucket = {"tokens": max_rate - 1, "last_update": now}
        _token_bucket[key] = bucket

    elapsed = now - bucket["last_update"]
    bucket["tokens"] = min(max_rate, bucket["tokens"] + elapsed * max_rate / window_seconds)
    bucket["last_update"] = now

    if bucket["tokens"] >= 1.0:
        bucket["tokens"] -= 1.0
        return True
    return False


def _rate_limit_key(model: str) -> str:
    return f"llm:{model}"


# ---- 熔断装饰器 ----
# circuitbreaker 默认：5 次失败打开，60s 超时，成功后半开
@circuit(failure_threshold=5, recovery_timeout=30, expected_exception=Exception)
def _invoke_with_circuit(llm: ChatOpenAI, messages: list, **kwargs):
    return llm.invoke(messages, **kwargs)


async def _ainvoke_with_circuit(llm: ChatOpenAI, messages: list, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _invoke_with_circuit(llm, messages, **kwargs))


# ---- 重试包装 ----
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((Exception,)),
    reraise=True,
)
async def _ainvoke_with_retry(llm: ChatOpenAI, messages: list, **kwargs):
    return await _ainvoke_with_circuit(llm, messages, **kwargs)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((Exception,)),
    reraise=True,
)
def _invoke_with_retry(llm: ChatOpenAI, messages: list, **kwargs):
    return _invoke_with_circuit(llm, messages, **kwargs)


class ResilientLLM:
    """带治理的 LLM 包装类，接口与 ChatOpenAI 兼容。"""

    def __init__(
        self,
        temperature: float,
        streaming: bool,
        max_tokens: Optional[int] = None,
        fallback_model: Optional[str] = None,
        fallback_api_key: Optional[str] = None,
        fallback_base_url: Optional[str] = None,
        rate_limit: float = 10.0,
    ):
        self.temperature = temperature
        self.streaming = streaming
        self.max_tokens = max_tokens
        # 从 settings 读取主模型配置（settings 自己从 .env 读取）
        if settings is not None:
            self.primary_model = settings.llm_model
            self.fallback_model = fallback_model or settings.llm_fallback_model or ""
            self.fallback_api_key = fallback_api_key or settings.llm_fallback_api_key or ""
            self.fallback_base_url = fallback_base_url or settings.llm_fallback_base_url or ""
        else:
            self.primary_model = os.environ.get("LLM_MODEL", "qwen-plus")
            self.fallback_model = fallback_model or os.environ.get("LLM_FALLBACK_MODEL", "")
            self.fallback_api_key = fallback_api_key or os.environ.get("LLM_FALLBACK_API_KEY", "")
            self.fallback_base_url = fallback_base_url or os.environ.get("LLM_FALLBACK_BASE_URL", "")
        self.rate_limit = rate_limit
        self.last_usage: Optional[UsageRecord] = None

    def with_config(self, config: dict) -> "ResilientLLM":
        """兼容 LangGraph 的 .with_config({"tags": [...]}) 调用。"""
        # ResilientLLM 本身无状态，直接返回 self；tags 由调用方传进 kwargs 或 config
        return self

    def _get_primary(self) -> ChatOpenAI:
        return _get_llm_raw(self.primary_model, self.temperature, self.streaming, self.max_tokens)

    def _get_fallback(self) -> Optional[ChatOpenAI]:
        if not self.fallback_model or self.fallback_model == self.primary_model:
            return None
        return _get_llm_raw(
            self.fallback_model, self.temperature, self.streaming, self.max_tokens,
            api_key=self.fallback_api_key or None,
            base_url=self.fallback_base_url or None,
        )

    def _check_rate_limit(self, model: str) -> bool:
        if self.rate_limit <= 0:
            return True
        allowed = _acquire_rate_limit(_rate_limit_key(model), max_rate=self.rate_limit)
        if not allowed:
            logger.warning(f"LLM 限流触发: {model}")
        return allowed

    async def ainvoke(self, messages: list, **kwargs) -> Any:
        self.last_usage = None

        if not self._check_rate_limit(self.primary_model):
            raise RuntimeError(f"LLM rate limit exceeded for {self.primary_model}")

        try:
            llm = self._get_primary()
            response = await _ainvoke_with_retry(llm, messages, **kwargs)
            self.last_usage = _extract_usage(response, self.primary_model)
            logger.info(f"LLM primary invoke success: {self.primary_model}, usage={self.last_usage.to_dict()}")
            return response
        except Exception as e:
            logger.warning(f"LLM primary {self.primary_model} 失败: {e}")
            fallback = self._get_fallback()
            if fallback is None:
                raise
            if not self._check_rate_limit(self.fallback_model):
                raise RuntimeError(f"LLM fallback rate limit exceeded for {self.fallback_model}")
            try:
                response = await _ainvoke_with_retry(fallback, messages, **kwargs)
                self.last_usage = _extract_usage(response, self.fallback_model)
                logger.info(f"LLM fallback invoke success: {self.fallback_model}, usage={self.last_usage.to_dict()}")
                return response
            except Exception as fe:
                logger.error(f"LLM fallback {self.fallback_model} 也失败: {fe}")
                raise

    def invoke(self, messages: list, **kwargs) -> Any:
        self.last_usage = None

        if not self._check_rate_limit(self.primary_model):
            raise RuntimeError(f"LLM rate limit exceeded for {self.primary_model}")

        try:
            llm = self._get_primary()
            response = _invoke_with_retry(llm, messages, **kwargs)
            self.last_usage = _extract_usage(response, self.primary_model)
            logger.info(f"LLM primary invoke success: {self.primary_model}, usage={self.last_usage.to_dict()}")
            return response
        except Exception as e:
            logger.warning(f"LLM primary {self.primary_model} 失败: {e}")
            fallback = self._get_fallback()
            if fallback is None:
                raise
            if not self._check_rate_limit(self.fallback_model):
                raise RuntimeError(f"LLM fallback rate limit exceeded for {self.fallback_model}")
            try:
                response = _invoke_with_retry(fallback, messages, **kwargs)
                self.last_usage = _extract_usage(response, self.fallback_model)
                logger.info(f"LLM fallback invoke success: {self.fallback_model}, usage={self.last_usage.to_dict()}")
                return response
            except Exception as fe:
                logger.error(f"LLM fallback {self.fallback_model} 也失败: {fe}")
                raise

    async def astream(self, messages: list, **kwargs):
        """流式调用：优先主模型，失败切 fallback。

        流式下 usage 通常只有最后一个 chunk 才有，或者完全没有；
        这里记录 model，token 数由调用方在收集完流后通过其他方式估算。
        """
        self.last_usage = None

        if not self._check_rate_limit(self.primary_model):
            raise RuntimeError(f"LLM rate limit exceeded for {self.primary_model}")

        try:
            llm = self._get_primary()
            async for chunk in llm.astream(messages, **kwargs):
                yield chunk
            self.last_usage = UsageRecord()
            self.last_usage.model = self.primary_model
            logger.info(f"LLM primary stream success: {self.primary_model}")
        except Exception as e:
            logger.warning(f"LLM primary stream {self.primary_model} 失败: {e}")
            fallback = self._get_fallback()
            if fallback is None:
                raise
            if not self._check_rate_limit(self.fallback_model):
                raise RuntimeError(f"LLM fallback rate limit exceeded for {self.fallback_model}")
            async for chunk in fallback.astream(messages, **kwargs):
                yield chunk
            self.last_usage = UsageRecord()
            self.last_usage.model = self.fallback_model
            logger.info(f"LLM fallback stream success: {self.fallback_model}")


def get_llm(temperature: float = None, streaming: bool = False, max_tokens: Optional[int] = None) -> ResilientLLM:
    """获取带治理的 LLM 包装实例。配置从 settings (.env) 读取。"""
    if temperature is None:
        temp = 0.7
    else:
        temp = temperature
    # 从 settings 读取 fallback 配置（settings 自己从 .env 读取）
    _fallback_model = settings.llm_fallback_model if settings and settings.llm_fallback_model else os.environ.get("LLM_FALLBACK_MODEL")
    _fallback_api_key = settings.llm_fallback_api_key if settings and settings.llm_fallback_api_key else os.environ.get("LLM_FALLBACK_API_KEY")
    _fallback_base_url = settings.llm_fallback_base_url if settings and settings.llm_fallback_base_url else os.environ.get("LLM_FALLBACK_BASE_URL")
    return ResilientLLM(
        temperature=temp,
        streaming=streaming,
        max_tokens=max_tokens,
        fallback_model=_fallback_model,
        fallback_api_key=_fallback_api_key,
        fallback_base_url=_fallback_base_url,
        rate_limit=float(os.environ.get("LLM_RATE_LIMIT", "10")),
    )


def get_last_usage(llm: ResilientLLM) -> Optional[dict]:
    """读取上一次 LLM 调用的 usage。"""
    if llm.last_usage:
        return llm.last_usage.to_dict()
    return None
