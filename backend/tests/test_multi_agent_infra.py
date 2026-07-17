"""Multi-Agent 基础设施层单元测试。"""
import asyncio
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
import pytest_asyncio

from app.agent.skill_registry import Skill, SkillRegistry
from app.agent.shared_state import SharedStateManager
from app.agent.message import AgentMessage
from app.agent.reliability import (
    agent_fallback,
    circuit_breaker,
    CircuitBreakerOpenError,
    CircuitState,
    DEFAULT_TIMEOUT,
)


# ===================================================================
# SkillRegistry 测试
# ===================================================================


class TestSkillRegistry:
    """测试 SkillRegistry 注册、执行、序列化。"""

    def test_register_and_get_skill(self):
        """注册技能后能正确获取。"""
        registry = SkillRegistry()
        skill = Skill(
            name="search",
            description="搜索商品",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            impl=lambda **kw: "result",
        )
        registry.register(skill)
        assert registry.get("search") is skill

    def test_register_overwrite_warning(self):
        """同名技能注册会覆盖。"""
        registry = SkillRegistry()
        skill1 = Skill(name="s", description="d1", parameters={}, impl=lambda **kw: 1)
        skill2 = Skill(name="s", description="d2", parameters={}, impl=lambda **kw: 2)
        registry.register(skill1)
        registry.register(skill2)
        assert registry.get("s").description == "d2"

    def test_list_skills(self):
        """列出所有已注册技能。"""
        registry = SkillRegistry()
        s1 = Skill(name="a", description="d", parameters={}, impl=lambda **kw: None)
        s2 = Skill(name="b", description="d", parameters={}, impl=lambda **kw: None)
        registry.register(s1)
        registry.register(s2)
        assert len(registry.list_skills()) == 2

    def test_to_openai_functions(self):
        """转换为 OpenAI function calling 格式。"""
        registry = SkillRegistry()
        skill = Skill(
            name="get_weather",
            description="获取天气",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            impl=lambda **kw: None,
        )
        registry.register(skill)
        functions = registry.to_openai_functions()
        assert len(functions) == 1
        assert functions[0]["type"] == "function"
        assert functions[0]["function"]["name"] == "get_weather"
        assert "city" in functions[0]["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_execute_sync_skill(self):
        """执行同步技能。"""
        registry = SkillRegistry()
        skill = Skill(
            name="add",
            description="加法",
            parameters={"type": "object"},
            impl=lambda a, b: a + b,
        )
        registry.register(skill)
        result = await registry.execute("add", a=2, b=3)
        assert result == 5

    @pytest.mark.asyncio
    async def test_execute_async_skill(self):
        """执行异步技能。"""
        registry = SkillRegistry()

        async def async_impl(x):
            await asyncio.sleep(0)
            return x * 2

        skill = Skill(
            name="double",
            description="翻倍",
            parameters={},
            impl=async_impl,
        )
        registry.register(skill)
        result = await registry.execute("double", x=5)
        assert result == 10

    def test_unregister_skill(self):
        """注销技能。"""
        registry = SkillRegistry()
        skill = Skill(name="temp", description="d", parameters={}, impl=lambda **kw: None)
        registry.register(skill)
        assert registry.unregister("temp") is True
        assert registry.get("temp") is None

    def test_contains_and_len(self):
        """测试 __contains__ 和 __len__。"""
        registry = SkillRegistry()
        skill = Skill(name="s", description="d", parameters={}, impl=lambda **kw: None)
        registry.register(skill)
        assert "s" in registry
        assert len(registry) == 1


# ===================================================================
# SharedStateManager 测试（使用 fakeredis）
# ===================================================================


class TestSharedStateManager:
    """测试 SharedStateManager 基于 Redis 的共享状态。"""

    @pytest_asyncio.fixture
    async def manager(self):
        """创建使用 fakeredis 的 SharedStateManager。"""
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        mgr = SharedStateManager()
        mgr._redis = r
        yield mgr
        await r.aclose()

    @pytest.mark.asyncio
    async def test_set_and_get_agent_result(self, manager):
        """设置并获取 agent 结果。"""
        await manager.set_agent_result("task-1", "agent-a", {"score": 0.95})
        result = await manager.get_agent_result("task-1", "agent-a")
        assert result == {"score": 0.95}

    @pytest.mark.asyncio
    async def test_get_state(self, manager):
        """获取任务的所有 agent 结果。"""
        await manager.set_agent_result("task-2", "agent-a", {"ok": True})
        await manager.set_agent_result("task-2", "agent-b", {"ok": False})
        state = await manager.get_state("task-2")
        assert state["agent-a"] == {"ok": True}
        assert state["agent-b"] == {"ok": False}

    @pytest.mark.asyncio
    async def test_get_nonexistent_result(self, manager):
        """获取不存在的结果返回 None。"""
        result = await manager.get_agent_result("no-task", "no-agent")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_agent_result_triggers_publish(self, manager):
        """set_agent_result 会触发 Pub/Sub 发布。"""
        # 订阅 channel
        pubsub = manager._redis.pubsub()
        await pubsub.subscribe("multiagent:event:task-3")
        # 设置结果
        await manager.set_agent_result("task-3", "agent-x", {"done": True})
        # 读取发布的事件
        msg = await pubsub.get_message(timeout=1.0)
        assert msg is not None
        assert msg["type"] == "subscribe"
        msg2 = await pubsub.get_message(timeout=1.0)
        assert msg2 is not None
        assert msg2["type"] == "message"
        assert "agent-x" in msg2["data"]
        await pubsub.unsubscribe()


# ===================================================================
# AgentMessage 测试
# ===================================================================


class TestAgentMessage:
    """测试 AgentMessage 编解码。"""

    def test_to_json_and_from_json(self):
        """序列化与反序列化。"""
        msg = AgentMessage(
            from_agent="planner",
            to_agent="executor",
            task_id="t-1",
            action="invoke",
            payload={"query": "test"},
        )
        json_str = msg.to_json()
        restored = AgentMessage.from_json(json_str)
        assert restored.from_agent == "planner"
        assert restored.to_agent == "executor"
        assert restored.task_id == "t-1"
        assert restored.action == "invoke"
        assert restored.payload == {"query": "test"}

    def test_create_invoke(self):
        """创建 invoke 消息。"""
        msg = AgentMessage.create_invoke("a", "b", "t", {"x": 1})
        assert msg.action == "invoke"
        assert msg.payload == {"x": 1}

    def test_create_result(self):
        """创建 result 消息。"""
        msg = AgentMessage.create_result("a", "b", "t", {"result": "ok"})
        assert msg.action == "result"
        assert msg.payload == {"result": "ok"}

    def test_create_error(self):
        """创建 error 消息。"""
        msg = AgentMessage.create_error("a", "b", "t", "something wrong")
        assert msg.action == "error"
        assert msg.payload == {"error": "something wrong"}

    def test_to_dict(self):
        """转为字典。"""
        msg = AgentMessage(from_agent="x", to_agent="y", task_id="z")
        d = msg.to_dict()
        assert d["from_agent"] == "x"
        assert d["to_agent"] == "y"
        assert d["task_id"] == "z"
        assert "message_id" in d
        assert "timestamp" in d

    def test_default_fields(self):
        """默认字段自动生成。"""
        msg = AgentMessage()
        assert msg.message_id != ""
        assert msg.timestamp != ""
        assert msg.payload == {}


# ===================================================================
# Reliability 装饰器测试
# ===================================================================


class TestAgentFallback:
    """测试 agent_fallback 装饰器。"""

    @pytest.mark.asyncio
    async def test_async_success(self):
        """异步函数正常返回。"""
        @agent_fallback(fallback_value="default")
        async def my_func():
            return "success"

        result = await my_func()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_async_timeout_fallback(self):
        """异步函数超时降级。"""
        @agent_fallback(fallback_value="timeout_fallback", timeout=0.1)
        async def slow_func():
            await asyncio.sleep(10)
            return "should_not_reach"

        result = await slow_func()
        assert result == "timeout_fallback"

    @pytest.mark.asyncio
    async def test_async_exception_fallback(self):
        """异步函数异常降级。"""
        @agent_fallback(fallback_value="error_fallback")
        async def error_func():
            raise ValueError("boom")

        result = await error_func()
        assert result == "error_fallback"

    def test_sync_exception_fallback(self):
        """同步函数异常降级。"""
        @agent_fallback(fallback_value="sync_fallback")
        def sync_error():
            raise RuntimeError("sync boom")

        result = sync_error()
        assert result == "sync_fallback"

    def test_sync_success(self):
        """同步函数正常返回。"""
        @agent_fallback(fallback_value="default")
        def sync_ok():
            return 42

        assert sync_ok() == 42


class TestCircuitBreaker:
    """测试 circuit_breaker 装饰器。"""

    @pytest.mark.asyncio
    async def test_success_not_triggered(self):
        """正常执行不触发熔断。"""
        @circuit_breaker(failure_threshold=3)
        async def stable_func():
            return "ok"

        for _ in range(5):
            result = await stable_func()
            assert result == "ok"

    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self):
        """连续失败后熔断器开启。"""
        @circuit_breaker(failure_threshold=3, recovery_timeout=10.0)
        async def failing_func():
            raise ValueError("fail")

        for _ in range(3):
            with pytest.raises(ValueError):
                await failing_func()

        # 熔断器已开启，下次调用直接抛 CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError):
            await failing_func()

    @pytest.mark.asyncio
    async def test_circuit_half_open_recovery(self):
        """熔断器半开后成功恢复。"""
        call_count = 0

        @circuit_breaker(failure_threshold=2, recovery_timeout=0.1)
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ValueError("fail")
            return "recovered"

        # 触发熔断
        for _ in range(2):
            with pytest.raises(ValueError):
                await flaky_func()

        # 熔断器开启
        with pytest.raises(CircuitBreakerOpenError):
            await flaky_func()

        # 等待恢复
        await asyncio.sleep(0.15)

        # 半开状态，成功调用后恢复
        result = await flaky_func()
        assert result == "recovered"

    @pytest.mark.asyncio
    async def test_default_timeout_constant(self):
        """DEFAULT_TIMEOUT 常量值。"""
        assert DEFAULT_TIMEOUT == 10.0
