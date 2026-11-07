"""Unit tests for ToolRegistry."""

from __future__ import annotations

import asyncio

import pytest

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort
from ravn.domain.models import ToolCall
from ravn.registry import ToolRegistrationError, ToolRegistry

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class SimpleTool(ToolPort):
    """A minimal tool that echoes back a value."""

    def __init__(self, tool_name: str = "simple") -> None:
        self._tool_name = tool_name

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return "A simple tool."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }

    @property
    def required_permission(self) -> str:
        return "tool:simple"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content=f"ok:{input.get('x', '')}")


class ErrorTool(ToolPort):
    """A tool that always raises an exception."""

    @property
    def name(self) -> str:
        return "error_tool"

    @property
    def description(self) -> str:
        return "Always raises."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return "tool:error"

    async def execute(self, input: dict) -> ToolResult:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_register_single_tool():
    reg = ToolRegistry()
    reg.register(SimpleTool())
    assert len(reg) == 1


def test_register_multiple_tools_increments_count():
    reg = ToolRegistry()
    reg.register(SimpleTool("a"))
    reg.register(SimpleTool("b"))
    assert len(reg) == 2


def test_list_returns_tools_in_order():
    reg = ToolRegistry()
    t1 = SimpleTool("first")
    t2 = SimpleTool("second")
    reg.register(t1)
    reg.register(t2)
    assert reg.list() == [t1, t2]


def test_list_returns_copy():
    reg = ToolRegistry()
    reg.register(SimpleTool())
    lst = reg.list()
    lst.clear()
    assert len(reg) == 1


def test_get_returns_registered_tool():
    reg = ToolRegistry()
    t = SimpleTool()
    reg.register(t)
    assert reg.get("simple") is t


def test_get_returns_none_for_unknown():
    reg = ToolRegistry()
    assert reg.get("nonexistent") is None


def test_collision_raises_registration_error():
    reg = ToolRegistry()
    reg.register(SimpleTool())
    with pytest.raises(ToolRegistrationError, match="already registered"):
        reg.register(SimpleTool())


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


def test_invalid_schema_type_raises():
    class BadTypeTool(SimpleTool):
        @property
        def input_schema(self) -> dict:
            return {"type": "notavalidtype"}

    with pytest.raises(ToolRegistrationError, match="invalid type"):
        ToolRegistry().register(BadTypeTool())


def test_schema_not_dict_raises():
    class NotDictTool(SimpleTool):
        @property
        def input_schema(self) -> dict:
            return "not a dict"  # type: ignore[return-value]

    with pytest.raises(ToolRegistrationError, match="must be a dict"):
        ToolRegistry().register(NotDictTool())


def test_properties_not_dict_raises():
    class BadPropertiesTool(SimpleTool):
        @property
        def input_schema(self) -> dict:
            return {"type": "object", "properties": "wrong"}

    with pytest.raises(ToolRegistrationError, match="'properties' must be a dict"):
        ToolRegistry().register(BadPropertiesTool())


def test_schema_without_type_is_valid():
    class NoTypeTool(SimpleTool):
        @property
        def input_schema(self) -> dict:
            return {"properties": {"x": {"type": "string"}}}

    reg = ToolRegistry()
    reg.register(NoTypeTool())
    assert len(reg) == 1


def test_all_valid_schema_types_accepted():
    valid_types = ["object", "array", "string", "integer", "number", "boolean", "null"]
    for schema_type in valid_types:

        class TypedTool(SimpleTool):
            _t = schema_type

            @property
            def name(self) -> str:
                return f"tool_{self._t}"

            @property
            def input_schema(self) -> dict:
                return {"type": self._t}

        reg = ToolRegistry()
        reg.register(TypedTool())  # should not raise


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_success_returns_result():
    reg = ToolRegistry()
    reg.register(SimpleTool())
    result = await reg.dispatch("simple", {"x": "hello"}, call_id="abc")
    assert not result.is_error
    assert result.content == "ok:hello"


@pytest.mark.asyncio
async def test_dispatch_sets_call_id():
    reg = ToolRegistry()
    reg.register(SimpleTool())
    result = await reg.dispatch("simple", {}, call_id="my-call-id")
    assert result.tool_call_id == "my-call-id"


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_returns_error():
    reg = ToolRegistry()
    result = await reg.dispatch("missing", {}, call_id="x")
    assert result.is_error
    assert "Unknown tool" in result.content
    assert result.tool_call_id == "x"


@pytest.mark.asyncio
async def test_dispatch_exception_returns_error_not_raises():
    reg = ToolRegistry()
    reg.register(ErrorTool())
    result = await reg.dispatch("error_tool", {}, call_id="y")
    assert result.is_error
    assert "boom" in result.content
    assert result.tool_call_id == "y"


@pytest.mark.asyncio
async def test_dispatch_error_preserves_call_id():
    reg = ToolRegistry()
    reg.register(ErrorTool())
    result = await reg.dispatch("error_tool", {}, call_id="err-id-42")
    assert result.tool_call_id == "err-id-42"


# ---------------------------------------------------------------------------
# parallelisable property tests
# ---------------------------------------------------------------------------


def test_tool_parallelisable_defaults_true():
    assert SimpleTool().parallelisable is True


def test_non_parallelisable_tool_can_override():
    class SequentialTool(SimpleTool):
        @property
        def parallelisable(self) -> bool:
            return False

    assert SequentialTool().parallelisable is False


# ---------------------------------------------------------------------------
# dispatch_batch tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_batch_empty_returns_empty():
    reg = ToolRegistry()
    results = await reg.dispatch_batch([])
    assert results == []


@pytest.mark.asyncio
async def test_dispatch_batch_single_call():
    reg = ToolRegistry()
    reg.register(SimpleTool())
    calls = [ToolCall(id="c1", name="simple", input={"x": "hi"})]
    results = await reg.dispatch_batch(calls)
    assert len(results) == 1
    assert results[0].tool_call_id == "c1"
    assert results[0].content == "ok:hi"
    assert not results[0].is_error


@pytest.mark.asyncio
async def test_dispatch_batch_multiple_parallelisable_tools():
    reg = ToolRegistry()
    reg.register(SimpleTool("a"))
    reg.register(SimpleTool("b"))
    calls = [
        ToolCall(id="id-a", name="a", input={"x": "alpha"}),
        ToolCall(id="id-b", name="b", input={"x": "beta"}),
    ]
    results = await reg.dispatch_batch(calls)
    assert len(results) == 2
    by_id = {r.tool_call_id: r for r in results}
    assert by_id["id-a"].content == "ok:alpha"
    assert by_id["id-b"].content == "ok:beta"


@pytest.mark.asyncio
async def test_dispatch_batch_preserves_order():
    """Results must be in the same order as the input calls."""
    reg = ToolRegistry()
    for name in ("t1", "t2", "t3"):
        reg.register(SimpleTool(name))
    calls = [ToolCall(id=f"id-{n}", name=n, input={"x": n}) for n in ("t1", "t2", "t3")]
    results = await reg.dispatch_batch(calls)
    assert [r.tool_call_id for r in results] == ["id-t1", "id-t2", "id-t3"]


@pytest.mark.asyncio
async def test_dispatch_batch_unknown_tool_returns_error_result():
    reg = ToolRegistry()
    calls = [ToolCall(id="x", name="no_such_tool", input={})]
    results = await reg.dispatch_batch(calls)
    assert len(results) == 1
    assert results[0].is_error
    assert "Unknown tool" in results[0].content


@pytest.mark.asyncio
async def test_dispatch_batch_error_tool_captured_not_raised():
    reg = ToolRegistry()
    reg.register(ErrorTool())
    calls = [ToolCall(id="e1", name="error_tool", input={})]
    results = await reg.dispatch_batch(calls)
    assert len(results) == 1
    assert results[0].is_error
    assert "boom" in results[0].content


@pytest.mark.asyncio
async def test_dispatch_batch_sequential_fallback_when_non_parallelisable():
    """When any tool is non-parallelisable the batch runs sequentially."""
    execution_order: list[str] = []

    class OrderedTool(ToolPort):
        def __init__(self, tool_name: str, delay: float = 0.0) -> None:
            self._tool_name = tool_name
            self._delay = delay

        @property
        def name(self) -> str:
            return self._tool_name

        @property
        def description(self) -> str:
            return "tracks order"

        @property
        def input_schema(self) -> dict:
            return {"type": "object", "properties": {}}

        @property
        def required_permission(self) -> str:
            return "tool:order"

        @property
        def parallelisable(self) -> bool:
            return False

        async def execute(self, input: dict) -> ToolResult:
            await asyncio.sleep(self._delay)
            execution_order.append(self._tool_name)
            return ToolResult(tool_call_id="", content=self._tool_name)

    reg = ToolRegistry()
    reg.register(OrderedTool("first", delay=0.05))
    reg.register(OrderedTool("second", delay=0.0))
    calls = [
        ToolCall(id="f", name="first", input={}),
        ToolCall(id="s", name="second", input={}),
    ]
    await reg.dispatch_batch(calls)
    # Sequential: "first" must complete before "second" starts
    assert execution_order == ["first", "second"]


@pytest.mark.asyncio
async def test_dispatch_batch_concurrent_when_all_parallelisable():
    """Parallel tools run concurrently — the batch completes faster than serial."""
    import time

    delay = 0.05

    class SlowTool(ToolPort):
        def __init__(self, tool_name: str) -> None:
            self._tool_name = tool_name

        @property
        def name(self) -> str:
            return self._tool_name

        @property
        def description(self) -> str:
            return "slow tool"

        @property
        def input_schema(self) -> dict:
            return {"type": "object", "properties": {}}

        @property
        def required_permission(self) -> str:
            return "tool:slow"

        async def execute(self, input: dict) -> ToolResult:
            await asyncio.sleep(delay)
            return ToolResult(tool_call_id="", content="done")

    reg = ToolRegistry()
    for n in ("s1", "s2", "s3"):
        reg.register(SlowTool(n))
    calls = [ToolCall(id=f"id-{n}", name=n, input={}) for n in ("s1", "s2", "s3")]

    start = time.monotonic()
    results = await reg.dispatch_batch(calls)
    elapsed = time.monotonic() - start

    assert len(results) == 3
    assert all(not r.is_error for r in results)
    # Concurrent: should complete in ~delay, not 3*delay
    assert elapsed < delay * 2


@pytest.mark.asyncio
async def test_dispatch_batch_mixed_parallelisable_falls_back_to_sequential():
    """A single non-parallelisable tool forces the whole batch sequential."""
    execution_order: list[str] = []

    class TrackingTool(ToolPort):
        def __init__(self, tool_name: str, parallel: bool = True, delay: float = 0.0) -> None:
            self._tool_name = tool_name
            self._parallel = parallel
            self._delay = delay

        @property
        def name(self) -> str:
            return self._tool_name

        @property
        def description(self) -> str:
            return "tracking"

        @property
        def input_schema(self) -> dict:
            return {"type": "object", "properties": {}}

        @property
        def required_permission(self) -> str:
            return "tool:track"

        @property
        def parallelisable(self) -> bool:
            return self._parallel

        async def execute(self, input: dict) -> ToolResult:
            await asyncio.sleep(self._delay)
            execution_order.append(self._tool_name)
            return ToolResult(tool_call_id="", content=self._tool_name)

    reg = ToolRegistry()
    reg.register(TrackingTool("parallel_a", parallel=True, delay=0.05))
    reg.register(TrackingTool("sequential_b", parallel=False, delay=0.0))
    calls = [
        ToolCall(id="pa", name="parallel_a", input={}),
        ToolCall(id="sb", name="sequential_b", input={}),
    ]
    await reg.dispatch_batch(calls)
    # Sequential fallback: parallel_a must finish before sequential_b starts
    assert execution_order == ["parallel_a", "sequential_b"]
