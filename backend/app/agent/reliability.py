"""Agent 可靠性装饰器：超时降级 + 异常降级 + 熔断器。"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 10.0


def agent_fallback(
    fallback_value: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable:
    """超时降级 + 异常降级装饰器。

    当被装饰函数执行超时时返回 fallback_value。
    当被装饰函数抛出指定异常时也返回 fallback_value。

    支持同步函数和异步函数。
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[agent_fallback] {func.__name__} 超时（{timeout}s），"
                    f"返回降级值: {fallback_value}"
                )
                return fallback_value
            except exceptions as e:
                logger.warning(
                    f"[agent_fallback] {func.__name__} 异常: {e}，"
                    f"返回降级值: {fallback_value}"
                )
                return fallback_value

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                logger.warning(
                    f"[agent_fallback] {func.__name__} 异常: {e}，"
                    f"返回降级值: {fallback_value}"
                )
                return fallback_value

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


class CircuitState(str, Enum):
    CLOSED = "closed"        # 正常状态
    OPEN = "open"            # 熔断状态
    HALF_OPEN = "half_open"  # 半开状态


class CircuitBreakerOpenError(Exception):
    """熔断器开启时抛出的异常。"""
    pass


def circuit_breaker(
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable:
    """熔断器装饰器。

    连续失败 N 次后进入熔断状态（直接返回异常，不再调用函数）。
    经过 recovery_timeout 后进入半开状态，允许一次尝试。
    若尝试成功则恢复，否则重新熔断。
    """
    def decorator(func: Callable) -> Callable:
        state = {
            "failures": 0,
            "state": CircuitState.CLOSED,
            "last_failure_time": 0.0,
        }

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            current_time = time.monotonic()

            if state["state"] == CircuitState.OPEN:
                if current_time - state["last_failure_time"] >= recovery_timeout:
                    state["state"] = CircuitState.HALF_OPEN
                    logger.info(f"[circuit_breaker] {func.__name__} 进入半开状态")
                else:
                    raise CircuitBreakerOpenError(
                        f"{func.__name__} 熔断器处于开启状态，"
                        f"还需 {recovery_timeout - (current_time - state['last_failure_time']):.1f}s 恢复"
                    )

            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                state["failures"] = 0
                state["state"] = CircuitState.CLOSED
                return result
            except exceptions as e:
                state["failures"] += 1
                state["last_failure_time"] = time.monotonic()
                if state["failures"] >= failure_threshold:
                    state["state"] = CircuitState.OPEN
                    logger.warning(
                        f"[circuit_breaker] {func.__name__} 连续失败 "
                        f"{state['failures']} 次，熔断器开启"
                    )
                raise

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            current_time = time.monotonic()

            if state["state"] == CircuitState.OPEN:
                if current_time - state["last_failure_time"] >= recovery_timeout:
                    state["state"] = CircuitState.HALF_OPEN
                    logger.info(f"[circuit_breaker] {func.__name__} 进入半开状态")
                else:
                    raise CircuitBreakerOpenError(
                        f"{func.__name__} 熔断器处于开启状态，"
                        f"还需 {recovery_timeout - (current_time - state['last_failure_time']):.1f}s 恢复"
                    )

            try:
                if asyncio.iscoroutinefunction(func):
                    raise RuntimeError(
                        "circuit_breaker 不支持同步上下文中的异步函数"
                    )
                result = func(*args, **kwargs)
                state["failures"] = 0
                state["state"] = CircuitState.CLOSED
                return result
            except exceptions as e:
                state["failures"] += 1
                state["last_failure_time"] = time.monotonic()
                if state["failures"] >= failure_threshold:
                    state["state"] = CircuitState.OPEN
                    logger.warning(
                        f"[circuit_breaker] {func.__name__} 连续失败 "
                        f"{state['failures']} 次，熔断器开启"
                    )
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
