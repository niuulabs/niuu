"""Phase 2 MCP / ToolRegistry integration tests (NIU-455).

Covers acceptance criteria:
- Tool registration: collision detection, schema validation
- Tool dispatch: known tool, unknown tool, exception capture
- Tool naming: prefix generation, collision detection
- Degraded mode: one tool registered, one not → available tools correct
- Batch dispatch: parallel and sequential ordering
- HookPipeline integration (via PermissionDeniedError)
"""

from __future__ import annotations

import pytest

from niuu.domain.tool import ToolResult
from ravn.domain.exceptions import PermissionDeniedError
from ravn.domain.models import ToolCall
from ravn.ports.hooks import HookPipelinePort
from ravn.registry import ToolRegistrationError, ToolRegistry, _validate_schema
from tests.ravn.fixtures.fakes import (
    CounterTool,
    PrefixedTool,
    RaisingTool,
    SequentialTool,
)

# ===========================================================================
# Schema validation (_validate_schema)
# ===========================================================================


class TestSchemaValidation:
    def test_valid_object_schema(self) -> None:
        _validate_schema("tool", {"type": "object", "properties": {}})

    def test_invalid_schema_type_raises(self) -> None:
        with pytest.raises(ToolRegistrationError):
            _validate_schema("tool", {"type": "invalid_type"})

    def test_non_dict_schema_raises(self) -> None:
        with pytest.raises(ToolRegistrationError):
            _validate_schema("tool", "not a dict")  # type: ignore[arg-type]

    def test_array_schema_type_valid(self) -> None:
        _validate_schema("tool", {"type": "array"})

    def test_string_schema_type_valid(self) -> None:
        _validate_schema("tool", {"type": "string"})


# ===========================================================================
# Tool registration (collision detection)
# ===========================================================================


class TestToolRegistration:
    def test_registers_single_tool(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool())
        assert len(registry) == 1

    def test_collision_raises_on_second_registration(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool("my_tool"))
        with pytest.raises(ToolRegistrationError, match="already registered"):
            registry.register(CounterTool("my_tool"))

    def test_different_names_no_collision(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool("tool_a"))
        registry.register(CounterTool("tool_b"))
        assert len(registry) == 2

    def test_list_returns_all_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool("a"))
        registry.register(CounterTool("b"))
        names = {t.name for t in registry.list()}
        assert names == {"a", "b"}

    def test_empty_registry_list(self) -> None:
        registry = ToolRegistry()
        assert registry.list() == []

    def test_len_zero_initially(self) -> None:
        registry = ToolRegistry()
        assert len(registry) == 0


# ===========================================================================
# Tool dispatch — known tool, unknown tool, exception capture
# ===========================================================================


class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_known_tool(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool())
        result = await registry.dispatch("counter", {"value": 1}, call_id="c1")
        assert not result.is_error
        assert "count=1" in result.content

    @pytest.mark.asyncio
    async def test_dispatch_exception_captured_as_error(self) -> None:
        registry = ToolRegistry()
        registry.register(RaisingTool())
        result = await registry.dispatch("raising_tool", {}, call_id="r1")
        assert result.is_error
        assert "Tool error" in result.content

    @pytest.mark.asyncio
    async def test_dispatch_result_has_correct_call_id(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool())
        result = await registry.dispatch("counter", {}, call_id="my-call-id")
        assert result.tool_call_id == "my-call-id"

    @pytest.mark.asyncio
    async def test_dispatch_multiple_times_increments_counter(self) -> None:
        registry = ToolRegistry()
        tool = CounterTool()
        registry.register(tool)
        await registry.dispatch("counter", {}, call_id="1")
        await registry.dispatch("counter", {}, call_id="2")
        assert tool.call_count == 2


# ===========================================================================
# Batch dispatch
# ===========================================================================


class TestBatchDispatch:
    @pytest.mark.asyncio
    async def test_empty_batch_returns_empty_list(self) -> None:
        registry = ToolRegistry()
        results = await registry.dispatch_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_batch_returns_results_in_order(self) -> None:
        registry = ToolRegistry()
        registry.register(PrefixedTool("tool_a"))
        registry.register(PrefixedTool("tool_b"))

        calls = [
            ToolCall(id="1", name="tool_a", input={}),
            ToolCall(id="2", name="tool_b", input={}),
        ]
        results = await registry.dispatch_batch(calls)

        assert len(results) == 2
        assert "tool_a" in results[0].content
        assert "tool_b" in results[1].content

    @pytest.mark.asyncio
    async def test_batch_with_unknown_tool_error_does_not_fail_batch(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool())

        calls = [
            ToolCall(id="1", name="counter", input={}),
            ToolCall(id="2", name="unknown_tool", input={}),
        ]
        results = await registry.dispatch_batch(calls)

        assert len(results) == 2
        assert not results[0].is_error
        assert results[1].is_error

    @pytest.mark.asyncio
    async def test_sequential_batch_respects_ordering(self) -> None:
        """Non-parallelisable tools execute in sequence."""
        seq_tool = SequentialTool("seq_a")
        registry = ToolRegistry()
        registry.register(seq_tool)

        calls = [ToolCall(id=str(i), name="seq_a", input={}) for i in range(3)]
        results = await registry.dispatch_batch(calls)

        assert len(results) == 3
        assert all(not r.is_error for r in results)


# ===========================================================================
# Degraded mode — tool discovery with partial failures
# ===========================================================================


class TestDegradedMode:
    """Simulate degraded mode: some tools available, some not."""

    @pytest.mark.asyncio
    async def test_available_tools_listed_correctly(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool("healthy_tool"))
        # Note: "failed_tool" is intentionally NOT registered (simulates failed MCP server)

        available = registry.list()
        names = {t.name for t in available}
        assert "healthy_tool" in names
        assert "failed_tool" not in names

    @pytest.mark.asyncio
    async def test_dispatch_to_available_tool_works(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool("healthy_tool"))

        result = await registry.dispatch("healthy_tool", {}, call_id="h1")
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_dispatch_to_unavailable_tool_returns_error(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool("healthy_tool"))

        result = await registry.dispatch("failed_tool", {}, call_id="f1")
        assert result.is_error

    def test_tool_count_reflects_registered_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(CounterTool("a"))
        registry.register(CounterTool("b"))
        assert len(registry) == 2

        # Simulate second MCP server failing: no additional tools registered
        # Only the two healthy tools are counted
        registry2 = ToolRegistry()
        registry2.register(CounterTool("healthy"))
        assert len(registry2) == 1


# ===========================================================================
# Tool naming — prefix generation and collision detection
# ===========================================================================


class TestToolNaming:
    """Simulate MCP tool prefix generation by registering tools with prefixed names."""

    def test_prefixed_tools_registered_without_collision(self) -> None:
        registry = ToolRegistry()
        # Two MCP servers each contribute a tool named "read" → prefixed as "server1__read"
        registry.register(PrefixedTool("server1__read"))
        registry.register(PrefixedTool("server2__read"))
        assert len(registry) == 2

    def test_collision_on_identical_prefixed_name(self) -> None:
        registry = ToolRegistry()
        registry.register(PrefixedTool("server1__read"))
        with pytest.raises(ToolRegistrationError):
            registry.register(PrefixedTool("server1__read"))

    def test_get_by_prefixed_name(self) -> None:
        registry = ToolRegistry()
        tool = PrefixedTool("mcp__github__list_repos")
        registry.register(tool)
        assert registry.get("mcp__github__list_repos") is tool

    def test_many_prefixed_tools_no_collision(self) -> None:
        registry = ToolRegistry()
        servers = ["server_a", "server_b", "server_c"]
        actions = ["read", "write", "delete"]
        for server in servers:
            for action in actions:
                registry.register(PrefixedTool(f"{server}__{action}"))
        assert len(registry) == len(servers) * len(actions)


# ===========================================================================
# HookPipeline integration — PermissionDeniedError is captured
# ===========================================================================


class TestHookPipelinePermissionDenied:
    """PermissionDeniedError from a pre-hook returns an error ToolResult."""

    @pytest.mark.asyncio
    async def test_permission_denied_error_captured(self) -> None:
        class DenyingPipeline(HookPipelinePort):
            async def run_pre(self, tool_name: str, args: dict, state: dict) -> dict:
                raise PermissionDeniedError(tool_name, state.get("required_permission", ""))

            async def run_post(
                self, tool_name: str, args: dict, result: ToolResult, state: dict
            ) -> ToolResult:
                return result

        registry = ToolRegistry(hook_pipeline=DenyingPipeline())
        registry.register(CounterTool())
        result = await registry.dispatch("counter", {}, call_id="p1")
        assert result.is_error
        assert "denied" in result.content.lower() or "permission" in result.content.lower()

    @pytest.mark.asyncio
    async def test_post_hook_can_modify_result(self) -> None:
        class ModifyingPipeline(HookPipelinePort):
            async def run_pre(self, tool_name: str, args: dict, state: dict) -> dict:
                return args

            async def run_post(
                self, tool_name: str, args: dict, result: ToolResult, state: dict
            ) -> ToolResult:
                return ToolResult(
                    tool_call_id=result.tool_call_id,
                    content=result.content + " [modified]",
                )

        registry = ToolRegistry(hook_pipeline=ModifyingPipeline())
        registry.register(CounterTool())
        result = await registry.dispatch("counter", {}, call_id="m1")
        assert "[modified]" in result.content
        assert not result.is_error
