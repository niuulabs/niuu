"""Execution-tape proof against the real native and CLI-backed harness paths."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from niuu.ports.cli import CLITransport, TransportCapabilities
from ravn.adapters.executors.cli import CliTransportAgent, _TransportBinding
from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission
from ravn.agent import RavnAgent
from ravn.domain.harness import HarnessTurnInput, HarnessTurnResult
from ravn.domain.models import (
    Message,
    Session,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from ravn.ports.native_compaction import NativeCompactionPort
from ravn.ports.tool import ToolPort
from tests.ravn.replay.execution_tape import ExecutionTape

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "execution_tapes" / "v1"


class _SilentChannel:
    async def emit(self, event: object) -> None:
        return None


class _ScriptedTool(ToolPort):
    @property
    def name(self) -> str:
        return "scripted"

    @property
    def description(self) -> str:
        return "Hermetic execution-tape tool."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {"call_id": {"type": "string"}}}

    @property
    def required_permission(self) -> str:
        return "tool:read"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id=input["call_id"], content="scripted result")


def _tool_calls(tape: ExecutionTape) -> list[ToolCall]:
    for event in tape.events:
        if event["kind"] != "model":
            continue
        calls = event["response"]["tool_calls"]
        if calls:
            return [
                ToolCall(
                    id=call["id"],
                    name=call["name"],
                    input={**call["input"], "call_id": call["id"]},
                )
                for call in calls
            ]
    raise AssertionError(f"{tape.case} has no scripted tool calls")


class _ScriptedNativeLLM:
    """Streams a tape's tool calls then a terminal response without provider I/O."""

    def __init__(self, tape: ExecutionTape) -> None:
        self._tool_calls = _tool_calls(tape)
        self._stream_count = 0

    async def stream(self, _system: object, **_: object) -> AsyncIterator[StreamEvent]:
        self._stream_count += 1
        if self._stream_count == 1:
            for call in self._tool_calls:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=call)
            yield StreamEvent(type=StreamEventType.MESSAGE_DONE, usage=TokenUsage(3, 2))
            return
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="Done")
        yield StreamEvent(type=StreamEventType.MESSAGE_DONE, usage=TokenUsage(3, 2))


class _ScriptedTapeTransport(CLITransport):
    """Emits the same tape events through the real CLI transport adapter."""

    def __init__(self, workspace_dir: str, *, tape: ExecutionTape, model: str = "") -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self.tape = tape
        self.model = model
        self._last_result: dict | None = None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_message(self, content: str) -> None:
        del content
        calls = _tool_calls(self.tape)
        await self._emit(
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                    for call in calls
                ],
            }
        )
        await self._emit(
            {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": "scripted result",
                        "is_error": False,
                    }
                    for call in calls
                ],
            }
        )
        self._last_result = {
            "type": "result",
            "result": "Done",
            "stop_reason": "end_turn",
            "modelUsage": {"scripted": {"inputTokens": 6, "outputTokens": 4}},
        }
        await self._emit(self._last_result)

    @property
    def session_id(self) -> str | None:
        return "scripted-cli-session"

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return True

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities()


@dataclass(frozen=True)
class _CanonicalHarnessOutcome:
    tool_order: tuple[str, ...]
    paired_tool_ids: tuple[str, ...]
    outcome: str
    usage_shape: str

    def canonical(self) -> dict[str, object]:
        return {
            "tool_order": self.tool_order,
            "paired_tool_ids": self.paired_tool_ids,
            "outcome": self.outcome,
            "usage_shape": self.usage_shape,
        }


def _canonical_outcome(result: HarnessTurnResult) -> _CanonicalHarnessOutcome:
    if result.turn.episode is None:
        raise AssertionError("harness result did not record an episode")
    return _CanonicalHarnessOutcome(
        tool_order=tuple(call.id for call in result.turn.tool_calls),
        paired_tool_ids=tuple(item.tool_call_id for item in result.turn.tool_results),
        outcome=result.turn.episode.outcome.value,
        usage_shape=(
            "reported"
            if result.turn.usage.input_tokens > 0 and result.turn.usage.output_tokens > 0
            else "unreported"
        ),
    )


def _assert_matches_tape(tape: ExecutionTape, actual: _CanonicalHarnessOutcome) -> None:
    expected = {field: tape.expected[field] for field in actual.canonical()}
    normalized_expected = {
        field: tuple(value) if isinstance(actual_value, tuple) else value
        for field, value in expected.items()
        for actual_value in (actual.canonical()[field],)
    }
    differences = {
        field: (normalized_expected[field], actual_value)
        for field, actual_value in actual.canonical().items()
        if normalized_expected[field] != actual_value
    }
    assert not differences, f"canonical harness replay differs: {differences}"


def _native_harness(tape: ExecutionTape, *, permission: object | None = None) -> RavnAgent:
    return RavnAgent(
        llm=_ScriptedNativeLLM(tape),  # type: ignore[arg-type]
        tools=[_ScriptedTool()],
        channel=_SilentChannel(),  # type: ignore[arg-type]
        permission=permission or AllowAllPermission(),  # type: ignore[arg-type]
        system_prompt="Replay only the recorded tape.",
        model="scripted",
        max_tokens=None,
        max_iterations=2,
    )


def _cli_harness(tape: ExecutionTape) -> CliTransportAgent:
    return CliTransportAgent(
        transport_binding=_TransportBinding(_ScriptedTapeTransport, True),
        transport_kwargs={"workspace_dir": ".", "tape": tape, "model": "scripted"},
        channel=_SilentChannel(),  # type: ignore[arg-type]
        system_prompt="Replay only the recorded tape.",
        session=Session(),
        model="scripted",
        max_iterations=2,
        checkpoint_port=None,
        task_id="execution-tape",
        persona="",
    )


@pytest.mark.asyncio
async def test_compatible_tape_runs_through_real_native_and_cli_harnesses() -> None:
    tape = ExecutionTape.load(_FIXTURE_ROOT / "multiple_tool_calls.json")
    native = _native_harness(tape)
    cli = _cli_harness(tape)
    await native.start()
    await cli.start()

    native_result = await native.run_turn(HarnessTurnInput("Replay the recorded turn."))
    cli_result = await cli.run_turn(HarnessTurnInput("Replay the recorded turn."))

    assert isinstance(native_result, HarnessTurnResult)
    assert isinstance(cli_result, HarnessTurnResult)
    native_outcome = _canonical_outcome(native_result)
    cli_outcome = _canonical_outcome(cli_result)
    _assert_matches_tape(tape, native_outcome)
    _assert_matches_tape(tape, cli_outcome)
    assert native_outcome.canonical() == cli_outcome.canonical()


@pytest.mark.asyncio
async def test_regressed_native_fold_is_detected_against_the_tape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tape = ExecutionTape.load(_FIXTURE_ROOT / "multiple_tool_calls.json")
    original = RavnAgent._execute_tool_observed

    async def misfold(self: RavnAgent, call: ToolCall) -> ToolResult:
        result = await original(self, call)
        return ToolResult(tool_call_id=f"lost-{result.tool_call_id}", content=result.content)

    monkeypatch.setattr(RavnAgent, "_execute_tool_observed", misfold)
    harness = _native_harness(tape)
    await harness.start()
    result = await harness.run_turn(HarnessTurnInput("Replay the recorded turn."))

    with pytest.raises(AssertionError, match="paired_tool_ids"):
        _assert_matches_tape(tape, _canonical_outcome(result))


@pytest.mark.asyncio
async def test_regressed_cli_ledger_is_detected_against_the_tape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tape = ExecutionTape.load(_FIXTURE_ROOT / "multiple_tool_calls.json")

    async def drop_result(self: CliTransportAgent, block: dict, correlation_id: str) -> None:
        del self, block, correlation_id

    monkeypatch.setattr(CliTransportAgent, "_record_tool_result_block", drop_result)
    harness = _cli_harness(tape)
    await harness.start()
    result = await harness.run_turn(HarnessTurnInput("Replay the recorded turn."))

    with pytest.raises(AssertionError, match="paired_tool_ids"):
        _assert_matches_tape(tape, _canonical_outcome(result))


@pytest.mark.asyncio
async def test_regressed_native_capability_check_is_detected_against_the_tape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tape = ExecutionTape.load(_FIXTURE_ROOT / "multiple_tool_calls.json")

    async def bypass_capability_check(self: RavnAgent, call: ToolCall) -> ToolResult:
        tool = self._tools[call.name]
        return await tool.execute(call.input)

    monkeypatch.setattr(RavnAgent, "_execute_tool_observed", bypass_capability_check)
    harness = _native_harness(tape, permission=DenyAllPermission())
    await harness.start()
    result = await harness.run_turn(HarnessTurnInput("Replay the recorded turn."))

    assert isinstance(result, HarnessTurnResult)
    with pytest.raises(AssertionError, match="capability"):
        assert all(item.is_error for item in result.turn.tool_results), (
            "native capability check allowed a denied tool dispatch"
        )


class _NativeCompaction(NativeCompactionPort):
    def enable_native_compaction(self, *, model: str, threshold_tokens: int) -> None:
        return None

    def compaction_window_start(self, messages: list[dict]) -> int:
        return 2


def _compacting_native_harness() -> RavnAgent:
    harness = RavnAgent(
        llm=object(),  # type: ignore[arg-type]
        tools=[],
        channel=_SilentChannel(),  # type: ignore[arg-type]
        permission=AllowAllPermission(),
        system_prompt="Replay only the recorded tape.",
        model="scripted",
        max_tokens=None,
        max_iterations=1,
        native_compaction=_NativeCompaction(),
    )
    for index in range(3):
        harness.session.add_message(Message(role="user", content=f"message-{index}"))
    return harness


@pytest.mark.asyncio
async def test_regressed_native_compaction_is_detected_against_the_tape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tape = ExecutionTape.load(_FIXTURE_ROOT / "compaction.json")
    assert tape.expected["context_safe"] is True

    baseline = _compacting_native_harness()
    assert [
        message.content
        for message in await baseline._maybe_compress("Replay only the recorded tape.")
    ] == ["message-2"]

    def skip_compaction(self: RavnAgent) -> list[Message]:
        return self.session.messages

    monkeypatch.setattr(RavnAgent, "_drop_natively_compacted_history", skip_compaction)
    harness = _compacting_native_harness()
    remaining = await harness._maybe_compress("Replay only the recorded tape.")

    with pytest.raises(AssertionError, match="protected context"):
        assert [message.content for message in remaining] == ["message-2"], (
            "native compaction did not retain only the provider-protected context"
        )
