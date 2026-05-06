"""Tests for OtelEventSink adapter.

Since opentelemetry-sdk is an optional dependency, these tests mock
the OTel API objects and verify the sink maps SessionEvents to the
correct gen_ai.* semantic convention attributes.
"""

import pytest

pytest.importorskip("opentelemetry", reason="opentelemetry not installed")

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from volundr.domain.models import SessionEvent, SessionEventType


def _make_event(**overrides) -> SessionEvent:
    defaults = {
        "id": uuid4(),
        "session_id": uuid4(),
        "event_type": SessionEventType.MESSAGE_ASSISTANT,
        "timestamp": datetime.now(UTC),
        "data": {"content_preview": "hello", "finish_reason": "end_turn"},
        "sequence": 0,
        "tokens_in": 100,
        "tokens_out": 50,
        "cost": Decimal("0.003"),
        "model": "claude-sonnet-4-20250514",
    }
    defaults.update(overrides)
    return SessionEvent(**defaults)


def _make_sink():
    """Create OtelEventSink with mocked OTel providers."""
    # Mock the opentelemetry modules
    mock_tracer_provider = MagicMock()
    mock_meter_provider = MagicMock()

    mock_tracer = MagicMock()
    mock_meter = MagicMock()
    mock_histogram = MagicMock()

    # Mock span context manager
    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)
    mock_tracer.start_as_current_span.return_value = mock_span

    mock_meter.create_histogram.return_value = mock_histogram

    with (
        patch("opentelemetry.trace.get_tracer", return_value=mock_tracer),
        patch("opentelemetry.metrics.get_meter", return_value=mock_meter),
    ):
        from volundr.adapters.outbound.otel_event_sink import OtelEventSink

        sink = OtelEventSink(
            tracer_provider=mock_tracer_provider,
            meter_provider=mock_meter_provider,
            service_name="test-volundr",
            provider_name="anthropic",
        )

    return sink, mock_tracer, mock_span, mock_histogram


class TestOtelEventSinkProperties:
    """Tests for sink properties."""

    def test_sink_name(self):
        sink, _, _, _ = _make_sink()
        assert sink.sink_name == "otel"

    def test_healthy_default(self):
        sink, _, _, _ = _make_sink()
        assert sink.healthy is True

    async def test_close_sets_unhealthy(self):
        sink, _, _, _ = _make_sink()
        await sink.close()
        assert sink.healthy is False


class TestOtelSpanEmission:
    """Tests for span creation with gen_ai.* attributes."""

    async def test_message_assistant_creates_chat_span(self):
        sink, tracer, _span, _ = _make_sink()
        event = _make_event()

        await sink.emit(event)

        tracer.start_as_current_span.assert_called_once()
        call_args = tracer.start_as_current_span.call_args
        assert call_args[0][0] == "chat claude-sonnet-4-20250514"

    async def test_span_has_required_genai_attributes(self):
        sink, _, span, _ = _make_sink()
        event = _make_event()

        await sink.emit(event)

        attr_calls = {c[0][0]: c[0][1] for c in span.set_attribute.call_args_list}
        assert attr_calls["gen_ai.operation.name"] == "chat"
        assert attr_calls["gen_ai.provider.name"] == "anthropic"
        assert attr_calls["gen_ai.request.model"] == "claude-sonnet-4-20250514"
        assert attr_calls["gen_ai.response.model"] == "claude-sonnet-4-20250514"

    async def test_span_has_token_usage_attributes(self):
        sink, _, span, _ = _make_sink()
        event = _make_event(tokens_in=200, tokens_out=100)

        await sink.emit(event)

        attr_calls = {c[0][0]: c[0][1] for c in span.set_attribute.call_args_list}
        assert attr_calls["gen_ai.usage.input_tokens"] == 200
        assert attr_calls["gen_ai.usage.output_tokens"] == 100

    async def test_span_has_conversation_id(self):
        sink, _, span, _ = _make_sink()
        session_id = uuid4()
        event = _make_event(session_id=session_id)

        await sink.emit(event)

        attr_calls = {c[0][0]: c[0][1] for c in span.set_attribute.call_args_list}
        assert attr_calls["gen_ai.conversation.id"] == str(session_id)

    async def test_span_has_finish_reason(self):
        sink, _, span, _ = _make_sink()
        event = _make_event(data={"finish_reason": "end_turn", "content_preview": "hi"})

        await sink.emit(event)

        attr_calls = {c[0][0]: c[0][1] for c in span.set_attribute.call_args_list}
        assert attr_calls["gen_ai.response.finish_reasons"] == ["end_turn"]

    async def test_file_modified_creates_execute_tool_span(self):
        sink, tracer, span, _ = _make_sink()
        event = _make_event(
            event_type=SessionEventType.FILE_MODIFIED,
            data={"path": "/src/main.py"},
        )

        await sink.emit(event)

        call_args = tracer.start_as_current_span.call_args
        assert call_args[0][0] == "execute_tool file_edit"

        attr_calls = {c[0][0]: c[0][1] for c in span.set_attribute.call_args_list}
        assert attr_calls["gen_ai.tool.name"] == "file_edit"
        assert attr_calls["gen_ai.tool.type"] == "function"

    async def test_terminal_command_creates_bash_tool_span(self):
        sink, tracer, _, _ = _make_sink()
        event = _make_event(
            event_type=SessionEventType.TERMINAL_COMMAND,
            data={"command": "npm test"},
        )

        await sink.emit(event)

        call_args = tracer.start_as_current_span.call_args
        assert call_args[0][0] == "execute_tool bash"

    async def test_session_start_creates_invoke_agent_span(self):
        sink, tracer, span, _ = _make_sink()
        event = _make_event(
            event_type=SessionEventType.SESSION_START,
            data={"session_name": "my-session", "model": "sonnet"},
        )

        await sink.emit(event)

        call_args = tracer.start_as_current_span.call_args
        assert call_args[0][0] == "invoke_agent my-session"

        attr_calls = {c[0][0]: c[0][1] for c in span.set_attribute.call_args_list}
        assert attr_calls["gen_ai.agent.name"] == "my-session"

    async def test_error_event_sets_span_error_status(self):
        sink, _, span, _ = _make_sink()
        event = _make_event(
            event_type=SessionEventType.ERROR,
            data={"source": "api_error", "message": "rate limit"},
        )

        with patch("opentelemetry.trace.StatusCode") as mock_status:
            mock_status.ERROR = "ERROR"
            await sink.emit(event)

        span.set_status.assert_called_once()

    async def test_git_commit_maps_correctly(self):
        sink, _tracer, span, _ = _make_sink()
        event = _make_event(
            event_type=SessionEventType.GIT_COMMIT,
            data={"hash": "abc123", "message": "fix bug"},
        )

        await sink.emit(event)

        attr_calls = {c[0][0]: c[0][1] for c in span.set_attribute.call_args_list}
        assert attr_calls["gen_ai.operation.name"] == "execute_tool"
        assert attr_calls["gen_ai.tool.name"] == "git_commit"


class TestOtelMetricEmission:
    """Tests for metric recording."""

    async def test_token_usage_histogram_recorded(self):
        sink, _, _, histogram = _make_sink()
        event = _make_event(tokens_in=200, tokens_out=100)

        await sink.emit(event)

        # Two histogram recordings: one for input, one for output
        assert histogram.record.call_count == 2

        calls = histogram.record.call_args_list
        # Input tokens
        assert calls[0][0][0] == 200
        assert calls[0][1]["attributes"]["gen_ai.token.type"] == "input"
        # Output tokens
        assert calls[1][0][0] == 100
        assert calls[1][1]["attributes"]["gen_ai.token.type"] == "output"

    async def test_duration_histogram_recorded(self):
        sink, _, _, histogram = _make_sink()
        event = _make_event(duration_ms=1500)

        await sink.emit(event)

        # Find duration recording (third call after token input + output)
        duration_calls = [
            c
            for c in histogram.record.call_args_list
            if c[0][0] == 1.5  # 1500ms -> 1.5s
        ]
        assert len(duration_calls) == 1

    async def test_no_tokens_skips_histogram(self):
        sink, _, _, histogram = _make_sink()
        event = _make_event(tokens_in=None, tokens_out=None, duration_ms=None)

        await sink.emit(event)

        histogram.record.assert_not_called()

    async def test_metric_attributes_include_model(self):
        sink, _, _, histogram = _make_sink()
        event = _make_event(
            tokens_in=50,
            tokens_out=0,
            model="claude-opus-4-20250514",
        )

        await sink.emit(event)

        call_attrs = histogram.record.call_args_list[0][1]["attributes"]
        assert call_attrs["gen_ai.request.model"] == "claude-opus-4-20250514"
        assert call_attrs["gen_ai.provider.name"] == "anthropic"


class TestOtelBatchAndFlush:
    """Tests for batch emission and flush."""

    async def test_emit_batch_creates_span_per_event(self):
        sink, tracer, _, _ = _make_sink()
        events = [_make_event(sequence=i) for i in range(3)]

        await sink.emit_batch(events)

        assert tracer.start_as_current_span.call_count == 3

    async def test_flush_is_noop(self):
        sink, _, _, _ = _make_sink()
        # Should not raise
        await sink.flush()


class TestExtractToolName:
    """Tests for _extract_tool_name helper."""

    def test_all_event_types_mapped(self):
        from volundr.adapters.outbound.otel_event_sink import OtelEventSink

        mappings = {
            SessionEventType.FILE_CREATED: "file_write",
            SessionEventType.FILE_MODIFIED: "file_edit",
            SessionEventType.FILE_DELETED: "file_delete",
            SessionEventType.GIT_COMMIT: "git_commit",
            SessionEventType.GIT_PUSH: "git_push",
            SessionEventType.GIT_BRANCH: "git_branch",
            SessionEventType.GIT_CHECKOUT: "git_checkout",
            SessionEventType.TERMINAL_COMMAND: "bash",
        }
        for event_type, expected_name in mappings.items():
            event = _make_event(event_type=event_type)
            assert OtelEventSink._extract_tool_name(event) == expected_name

    def test_tool_use_extracts_from_data(self):
        from volundr.adapters.outbound.otel_event_sink import OtelEventSink

        event = _make_event(
            event_type=SessionEventType.TOOL_USE,
            data={"tool": "Read"},
        )
        assert OtelEventSink._extract_tool_name(event) == "Read"
