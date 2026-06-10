"""Shared test fakes for the Ravn test suite.

Imported by both tests/ravn/conftest.py and tests/test_ravn/conftest.py
so there is a single authoritative definition of each fake.
"""

from __future__ import annotations

from ravn.domain.events import RavnEvent
from ravn.domain.models import ToolResult
from ravn.ports.channel import ChannelPort
from ravn.ports.tool import ToolPort


class InMemoryChannel(ChannelPort):
    """Records every event emitted by the agent for inspection in tests."""

    def __init__(self) -> None:
        self.events: list[RavnEvent] = []

    async def emit(self, event: RavnEvent) -> None:
        self.events.append(event)


class EchoTool(ToolPort):
    """Returns the ``message`` input as its result."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the message back."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    @property
    def required_permission(self) -> str:
        return "tool:echo"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content=input.get("message", ""))


class FailingTool(ToolPort):
    """Always raises a RuntimeError when executed."""

    @property
    def name(self) -> str:
        return "fail"

    @property
    def description(self) -> str:
        return "Always fails."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return "tool:fail"

    async def execute(self, input: dict) -> ToolResult:
        raise RuntimeError("intentional failure")


class CounterTool(ToolPort):
    """Records the number of times it was called; supports a configurable name."""

    def __init__(self, name: str = "counter") -> None:
        self._name = name
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Counts calls."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
        }

    @property
    def required_permission(self) -> str:
        return "tool:counter"

    async def execute(self, input: dict) -> ToolResult:
        self.call_count += 1
        return ToolResult(tool_call_id="", content=f"count={self.call_count}")


class PrefixedTool(ToolPort):
    """Minimal tool with a caller-supplied name for prefix/naming tests."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Prefixed tool: {self._name}"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return "tool:any"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content=f"called:{self._name}")


class RaisingTool(ToolPort):
    """Raises RuntimeError when executed (for exception-capture tests)."""

    @property
    def name(self) -> str:
        return "raising_tool"

    @property
    def description(self) -> str:
        return "Always raises."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return "tool:raise"

    async def execute(self, input: dict) -> ToolResult:
        raise RuntimeError("intentional error")


class SequentialTool(ToolPort):
    """Non-parallelisable tool for sequential-dispatch ordering tests."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.order: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Sequential."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return "tool:seq"

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        self.order.append(self._name)
        return ToolResult(tool_call_id="", content=self._name)


class ManualClock:
    """Deterministic monotonic clock for time-travel in tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BusRecorder:
    """Capture Sleipnir events from an in-process bus and filter by type."""

    def __init__(self, bus) -> None:
        self._bus = bus
        self.events: list = []

    async def __call__(self, event) -> None:
        self.events.append(event)

    async def of_type(self, event_type: str) -> list:
        await self._bus.flush()
        return [event for event in self.events if event.event_type == event_type]
