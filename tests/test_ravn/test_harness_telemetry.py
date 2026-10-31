"""Cross-harness verification for canonical step telemetry."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import contextmanager

import pytest

from niuu.ports.cli import CLITransport
from ravn.adapters.executors.cli import CliTransportExecutor
from ravn.agent import RavnAgent
from ravn.domain.models import Session, StreamEvent, StreamEventType, TokenUsage
from ravn.harness_telemetry import HARNESS_STEP_EVENT
from ravn.ports.llm import LLMPort
from tests.ravn.fixtures.fakes import InMemoryChannel
from tests.test_ravn.conftest import AllowAllPermission


class _RecordingSpan:
    def set_attribute(self, _name: str, _value: object) -> None:
        return None


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    @contextmanager
    def span(self, _name: str, **_kwargs: object):
        yield _RecordingSpan()

    def event(self, name: str, **kwargs: object) -> None:
        self.events.append((name, kwargs))

    def set_attributes(self, _attributes: dict[str, object]) -> None:
        return None

    def count(self, *_args: object, **_kwargs: object) -> None:
        return None

    def duration(self, *_args: object, **_kwargs: object) -> None:
        return None

    def record(self, *_args: object, **_kwargs: object) -> None:
        return None

    def gauge(self, *_args: object, **_kwargs: object) -> None:
        return None

    def inject(self) -> dict[str, str]:
        return {}


class _ToolTimingCliTransport(CLITransport):
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_message(self, _content: str) -> None:
        await self._emit(
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {}},
                ],
            }
        )
        await self._emit(
            {
                "type": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"},
                ],
            }
        )
        self._last_result = {
            "type": "result",
            "result": "complete",
            "stop_reason": "end_turn",
            "modelUsage": {"test-model": {"inputTokens": 3, "outputTokens": 1}},
        }
        await self._emit(self._last_result)

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return True


class _OverlappingToolsCliTransport(CLITransport):
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_message(self, _content: str) -> None:
        await self._emit(
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {}},
                    {"type": "tool_use", "id": "tool-2", "name": "Bash", "input": {}},
                ],
            }
        )
        await self._emit(
            {
                "type": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"},
                    {"type": "tool_result", "tool_use_id": "tool-2", "content": "ok"},
                ],
            }
        )
        self._last_result = {
            "type": "result",
            "result": "complete",
            "stop_reason": "end_turn",
            "modelUsage": {"test-model": {"inputTokens": 3, "outputTokens": 1}},
        }
        await self._emit(self._last_result)

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return True


class _NoTextLLM(LLMPort):
    async def stream(self, *_args: object, **_kwargs: object) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(
                input_tokens=3,
                output_tokens=0,
                cache_read_tokens=4,
                cache_write_tokens=2,
                thinking_tokens=1,
            ),
        )

    async def generate(self, *_args: object, **_kwargs: object):
        raise AssertionError("generate is not used by a normal harness step")


class _CliTransport(CLITransport):
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_message(self, _content: str) -> None:
        self._last_result = {
            "type": "result",
            "result": "complete",
            "stop_reason": "end_turn",
            "modelUsage": {
                "test-model": {
                    "inputTokens": 3,
                    "outputTokens": 1,
                    "cacheReadInputTokens": 4,
                    "cacheCreationInputTokens": 2,
                    "thinkingTokens": 1,
                }
            },
        }
        await self._emit(self._last_result)

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_native_and_cli_emit_one_canonical_step_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    monkeypatch.setattr("ravn.agent.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    native = RavnAgent(
        llm=_NoTextLLM(),
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="test system",
        model="test-model",
        max_tokens=64,
        max_iterations=1,
        task_id="native-task",
    )
    await native.run_turn("test")

    cli = CliTransportExecutor(
        transport_adapter="tests.test_ravn.test_harness_telemetry._CliTransport"
    ).build(
        channel=InMemoryChannel(),
        system_prompt="test system",
        session=Session(),
        model="test-model",
        max_iterations=1,
        checkpoint_port=None,
        task_id="cli-task",
        persona="tester",
        workspace_dir="/tmp/workspace",
        permission_mode="workspace_write",
        tools=[],
    )
    await cli.run_turn("test")

    records = [kwargs["content"] for name, kwargs in telemetry.events if name == HARNESS_STEP_EVENT]
    assert len(records) == 2
    native_record, cli_record = records
    assert native_record.keys() == cli_record.keys()
    assert native_record["harness_type"] == "native"
    assert cli_record["harness_type"] == "cli-backed"
    assert native_record["context_section_estimates"] is not None
    assert cli_record["context_section_estimates"] is None
    assert cli_record["provider_duration_ms"] is not None
    assert "timing.provider_vs_tool_breakdown" not in cli_record["unavailable"]
    assert native_record["unavailable"]["timing.ttft_ms"] == "no_streamed_text_token"
    assert cli_record["unavailable"]["timing.ttft_ms"] == "no_streamed_text_token"
    for field in (
        "step_id",
        "phase",
        "model",
        "provider",
        "compaction_applied",
        "provider_duration_ms",
        "tool_wall_time_ms",
        "usage",
        "stop_reason",
        "llm_error_category",
        "recovery_state",
    ):
        assert field in native_record


@pytest.mark.asyncio
async def test_cli_provider_duration_excludes_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    clock = iter((0.0, 0.0, 0.0, 5.0, 7.0, 7.0))
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.monotonic", lambda: next(clock))
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    await _build_cli_agent(
        "tests.test_ravn.test_harness_telemetry._ToolTimingCliTransport"
    ).run_turn("test")

    [record] = _harness_records(telemetry)
    assert record["tool_wall_time_ms"] == pytest.approx(5_000)
    assert record["provider_duration_ms"] == pytest.approx(2_000)
    assert "timing.provider_vs_tool_breakdown" not in record["unavailable"]


@pytest.mark.asyncio
async def test_cli_provider_duration_subtracts_overlapping_tool_intervals_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    clock = iter((0.0, 0.0, 0.0, 0.0, 5.0, 5.0, 7.0, 7.0))
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.monotonic", lambda: next(clock))
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    await _build_cli_agent(
        "tests.test_ravn.test_harness_telemetry._OverlappingToolsCliTransport"
    ).run_turn("test")

    [record] = _harness_records(telemetry)
    assert record["tool_wall_time_ms"] == pytest.approx(10_000)
    assert record["provider_duration_ms"] == pytest.approx(2_000)


@pytest.mark.asyncio
async def test_cross_harness_step_events_export_without_content_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from niuu.observability import Observability

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=MeterProvider(),
        capture_content=False,
    )
    monkeypatch.setattr("ravn.agent.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    native = RavnAgent(
        llm=_NoTextLLM(),
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="test system",
        model="test-model",
        max_tokens=64,
        max_iterations=1,
        task_id="native-task",
    )
    await native.run_turn("test")

    cli = CliTransportExecutor(
        transport_adapter="tests.test_ravn.test_harness_telemetry._CliTransport"
    ).build(
        channel=InMemoryChannel(),
        system_prompt="test system",
        session=Session(),
        model="test-model",
        max_iterations=1,
        checkpoint_port=None,
        task_id="cli-task",
        persona="tester",
        workspace_dir="/tmp/workspace",
        permission_mode="workspace_write",
        tools=[],
    )
    await cli.run_turn("test")

    events = [
        event
        for span in exporter.get_finished_spans()
        for event in span.events
        if event.name == HARNESS_STEP_EVENT
    ]
    assert len(events) == 2
    for event in events:
        attributes = event.attributes
        assert "ravn.content" not in attributes
        assert attributes["ravn.harness.context.section_estimates.available"] in (True, False)
        assert attributes["gen_ai.usage.cache_read_tokens"] == 4
        assert attributes["gen_ai.usage.cache_write_tokens"] == 2
        assert attributes["gen_ai.usage.thinking_tokens"] == 1

    native_event = next(
        event for event in events if event.attributes["ravn.harness.type"] == "native"
    )
    cli_event = next(
        event for event in events if event.attributes["ravn.harness.type"] == "cli-backed"
    )
    assert native_event.attributes["ravn.harness.context.section_estimates.available"] is True
    assert "ravn.harness.context.section_estimates" in native_event.attributes
    assert cli_event.attributes["ravn.harness.context.section_estimates.available"] is False
    assert (
        cli_event.attributes["ravn.harness.context.section_estimates.unavailable_reason"]
        == "cli_transport_does_not_expose_prompt_sections"
    )
    assert any(
        span.name == "ravn harness step" and native_event in span.events
        for span in exporter.get_finished_spans()
    )

    telemetry.shutdown()


class _FailingLLM(LLMPort):
    async def stream(self, *_args: object, **_kwargs: object) -> AsyncIterator[StreamEvent]:
        raise RuntimeError("provider failure contains sensitive details")
        yield  # pragma: no cover

    async def generate(self, *_args: object, **_kwargs: object):
        raise AssertionError("generate is not used by a normal harness step")


class _AbortedLLM(LLMPort):
    async def stream(self, *_args: object, **_kwargs: object) -> AsyncIterator[StreamEvent]:
        raise asyncio.CancelledError
        yield  # pragma: no cover

    async def generate(self, *_args: object, **_kwargs: object):
        raise AssertionError("generate is not used by a normal harness step")


class _FailingAfterTextLLM(LLMPort):
    async def stream(self, *_args: object, **_kwargs: object) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="partial output")
        raise RuntimeError("provider failure after streamed text")

    async def generate(self, *_args: object, **_kwargs: object):
        raise AssertionError("generate is not used by a normal harness step")


class _FailingAfterUsageLLM(LLMPort):
    async def stream(self, *_args: object, **_kwargs: object) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(
                input_tokens=3,
                output_tokens=1,
                cache_read_tokens=4,
                cache_write_tokens=2,
                thinking_tokens=1,
            ),
        )
        raise RuntimeError("provider failure after usage")

    async def generate(self, *_args: object, **_kwargs: object):
        raise AssertionError("generate is not used by a normal harness step")


class _TerminalCliTransport(CLITransport):
    terminal_error: BaseException = RuntimeError("provider failure contains sensitive details")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_message(self, _content: str) -> None:
        raise self.terminal_error

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return None

    @property
    def is_alive(self) -> bool:
        return True


class _TerminalToolCliTransport(_TerminalCliTransport):
    async def send_message(self, _content: str) -> None:
        await self._emit(
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {}},
                ],
            }
        )
        raise self.terminal_error


class _AbortedCliTransport(_TerminalCliTransport):
    terminal_error = asyncio.CancelledError()


class _StartFailingCliTransport(_TerminalCliTransport):
    async def start(self) -> None:
        raise RuntimeError("CLI transport failed to start")


class _FailingAfterTextCliTransport(_TerminalCliTransport):
    async def send_message(self, _content: str) -> None:
        await self._emit({"type": "content_block_delta", "delta": {"text": "partial output"}})
        raise RuntimeError("provider failure after streamed text")


class _FailingAfterUsageCliTransport(_TerminalCliTransport):
    def __init__(self) -> None:
        super().__init__()
        self._last_result: dict | None = None

    async def send_message(self, _content: str) -> None:
        await self._emit(
            {
                "type": "result",
                "modelUsage": {
                    "test-model": {
                        "inputTokens": 3,
                        "outputTokens": 1,
                        "cacheReadInputTokens": 4,
                        "cacheCreationInputTokens": 2,
                        "thinkingTokens": 1,
                    }
                },
            }
        )
        self._last_result = {"type": "error", "is_error": True}
        raise RuntimeError("provider failure after usage")

    @property
    def last_result(self) -> dict | None:
        return self._last_result


def _build_cli_agent(transport_adapter: str) -> object:
    return CliTransportExecutor(transport_adapter=transport_adapter).build(
        channel=InMemoryChannel(),
        system_prompt="test system",
        session=Session(),
        model="test-model",
        max_iterations=1,
        checkpoint_port=None,
        task_id="cli-task",
        persona="tester",
        workspace_dir="/tmp/workspace",
        permission_mode="workspace_write",
        tools=[],
    )


def _build_native_agent(llm: LLMPort) -> RavnAgent:
    return RavnAgent(
        llm=llm,
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="test system",
        model="test-model",
        max_tokens=64,
        max_iterations=1,
        task_id="native-task",
    )


def _harness_records(telemetry: _RecordingTelemetry) -> list[dict[str, object]]:
    return [kwargs["content"] for name, kwargs in telemetry.events if name == HARNESS_STEP_EVENT]


@pytest.mark.asyncio
async def test_native_and_cli_emit_terminal_records_for_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    monkeypatch.setattr("ravn.agent.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    with pytest.raises(RuntimeError, match="sensitive details"):
        await _build_native_agent(_FailingLLM()).run_turn("test")
    with pytest.raises(RuntimeError, match="sensitive details"):
        await _build_cli_agent(
            "tests.test_ravn.test_harness_telemetry._TerminalCliTransport"
        ).run_turn("test")

    native_record, cli_record = _harness_records(telemetry)
    assert native_record["harness_type"] == "native"
    assert native_record["stop_reason"] == "error"
    assert native_record["provider_duration_ms"] is not None
    assert native_record["llm_error_category"] == "provider_error"
    assert native_record["unavailable"] == {
        "provider_call.id": "provider_does_not_expose_request_id",
        "usage": "provider_call_did_not_complete",
        "timing.ttft_ms": "provider_call_terminated_before_text",
    }
    assert cli_record["harness_type"] == "cli-backed"
    assert cli_record["stop_reason"] == "provider_error"
    assert cli_record["provider_duration_ms"] is not None
    assert cli_record["llm_error_category"] == "provider_error"
    assert cli_record["unavailable"]["usage"] == "provider_call_did_not_complete"
    assert "sensitive details" not in str(native_record)
    assert "sensitive details" not in str(cli_record)


@pytest.mark.asyncio
async def test_cli_terminal_provider_duration_excludes_unfinished_tool_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    clock = iter((0.0, 0.0, 0.0, 5.0, 5.0))
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.monotonic", lambda: next(clock))
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    with pytest.raises(RuntimeError, match="sensitive details"):
        await _build_cli_agent(
            "tests.test_ravn.test_harness_telemetry._TerminalToolCliTransport"
        ).run_turn("test")

    [record] = _harness_records(telemetry)
    assert record["provider_duration_ms"] == pytest.approx(0)
    assert record["tool_wall_time_ms"] is None
    assert record["unavailable"]["timing.tool_wall_time_ms"] == (
        "provider_call_terminated_before_tool_completion"
    )


@pytest.mark.asyncio
async def test_native_and_cli_emit_terminal_records_for_aborted_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    monkeypatch.setattr("ravn.agent.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    with pytest.raises(asyncio.CancelledError):
        await _build_native_agent(_AbortedLLM()).run_turn("test")
    with pytest.raises(asyncio.CancelledError):
        await _build_cli_agent(
            "tests.test_ravn.test_harness_telemetry._AbortedCliTransport"
        ).run_turn("test")

    records = _harness_records(telemetry)
    assert [record["stop_reason"] for record in records] == ["aborted", "aborted"]
    assert [record["recovery_state"] for record in records] == ["aborted", "aborted"]
    for record in records:
        assert record["llm_error_category"] == "aborted"
        assert record["usage"] is None
        assert record["ttft_ms"] is None
        assert record["unavailable"]["usage"] == "provider_call_did_not_complete"
        assert record["unavailable"]["timing.ttft_ms"] == "provider_call_terminated_before_text"
    assert records[1]["provider_duration_ms"] is not None


@pytest.mark.asyncio
async def test_cli_start_failure_emits_terminal_record_with_explicit_missing_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    with pytest.raises(RuntimeError, match="failed to start"):
        await _build_cli_agent(
            "tests.test_ravn.test_harness_telemetry._StartFailingCliTransport"
        ).run_turn("test")

    [record] = _harness_records(telemetry)
    assert record["stop_reason"] == "provider_error"
    assert record["llm_error_category"] == "provider_error"
    assert record["provider_duration_ms"] is None
    assert record["unavailable"]["timing.provider_duration_ms"] == (
        "cli_transport_turn_duration_not_observed"
    )


@pytest.mark.asyncio
async def test_terminal_records_preserve_usage_reported_before_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    monkeypatch.setattr("ravn.agent.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    with pytest.raises(RuntimeError, match="after usage"):
        await _build_native_agent(_FailingAfterUsageLLM()).run_turn("test")
    with pytest.raises(RuntimeError, match="after usage"):
        await _build_cli_agent(
            "tests.test_ravn.test_harness_telemetry._FailingAfterUsageCliTransport"
        ).run_turn("test")

    native_record, cli_record = _harness_records(telemetry)
    expected_usage = {
        "input_tokens": 3,
        "output_tokens": 1,
        "cache_read_tokens": 4,
        "cache_write_tokens": 2,
        "thinking_tokens": 1,
    }
    for record in (native_record, cli_record):
        assert record["usage"] == expected_usage
        assert "usage" not in record["unavailable"]


@pytest.mark.asyncio
async def test_terminal_records_preserve_ttft_after_streamed_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = _RecordingTelemetry()
    monkeypatch.setattr("ravn.agent.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.adapters.executors.cli.get_observability", lambda: telemetry)
    monkeypatch.setattr("ravn.harness_telemetry.get_observability", lambda: telemetry)

    with pytest.raises(RuntimeError, match="after streamed text"):
        await _build_native_agent(_FailingAfterTextLLM()).run_turn("test")
    with pytest.raises(RuntimeError, match="after streamed text"):
        await _build_cli_agent(
            "tests.test_ravn.test_harness_telemetry._FailingAfterTextCliTransport"
        ).run_turn("test")

    native_record, cli_record = _harness_records(telemetry)
    for record in (native_record, cli_record):
        assert record["ttft_ms"] is not None
        assert "timing.ttft_ms" not in record["unavailable"]
