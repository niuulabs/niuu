"""OpenTelemetry event sink — emits session events as OTel spans and metrics.

Maps SessionEvents to the OTel GenAI semantic conventions (v1.39+):
  - Spans follow gen_ai.* attribute naming
  - Metrics: gen_ai.client.token.usage (histogram), gen_ai.client.operation.duration
  - Provider name is always "anthropic" (Volundr runs Claude Code)

Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
"""

import logging
from typing import Any

from volundr.domain.models import SessionEvent, SessionEventType
from volundr.domain.ports import EventSink

logger = logging.getLogger(__name__)

# OTel GenAI semantic convention attribute keys
_ATTR_OPERATION_NAME = "gen_ai.operation.name"
_ATTR_PROVIDER_NAME = "gen_ai.provider.name"
_ATTR_REQUEST_MODEL = "gen_ai.request.model"
_ATTR_RESPONSE_MODEL = "gen_ai.response.model"
_ATTR_CONVERSATION_ID = "gen_ai.conversation.id"
_ATTR_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
_ATTR_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
_ATTR_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
_ATTR_TOOL_NAME = "gen_ai.tool.name"
_ATTR_TOOL_TYPE = "gen_ai.tool.type"
_ATTR_AGENT_NAME = "gen_ai.agent.name"
_ATTR_AGENT_ID = "gen_ai.agent.id"
_ATTR_TOKEN_TYPE = "gen_ai.token.type"

# Map SessionEventType -> OTel operation name
_EVENT_TO_OPERATION: dict[SessionEventType, str] = {
    SessionEventType.MESSAGE_USER: "chat",
    SessionEventType.MESSAGE_ASSISTANT: "chat",
    SessionEventType.TOKEN_USAGE: "chat",
    SessionEventType.TOOL_USE: "execute_tool",
    SessionEventType.FILE_CREATED: "execute_tool",
    SessionEventType.FILE_MODIFIED: "execute_tool",
    SessionEventType.FILE_DELETED: "execute_tool",
    SessionEventType.GIT_COMMIT: "execute_tool",
    SessionEventType.GIT_PUSH: "execute_tool",
    SessionEventType.GIT_BRANCH: "execute_tool",
    SessionEventType.GIT_CHECKOUT: "execute_tool",
    SessionEventType.TERMINAL_COMMAND: "execute_tool",
    SessionEventType.ERROR: "chat",
    SessionEventType.SESSION_START: "invoke_agent",
    SessionEventType.SESSION_STOP: "invoke_agent",
}


class OtelEventSink(EventSink):
    """OpenTelemetry adapter for the session event pipeline.

    Uses the OTel SDK TracerProvider and MeterProvider to emit:
    - A span per event (following gen_ai.* semantic conventions)
    - Token usage histograms (gen_ai.client.token.usage)
    - Operation duration histograms (gen_ai.client.operation.duration)

    Constructor accepts pre-built OTel objects so the caller controls
    the exporter backend (OTLP/gRPC to Tempo, Jaeger, stdout, etc.).
    """

    def __init__(
        self,
        tracer_provider: Any,
        meter_provider: Any,
        service_name: str = "volundr",
        provider_name: str = "anthropic",
    ):
        # Lazy imports to avoid hard dependency at module level
        from opentelemetry import metrics, trace

        self._tracer = trace.get_tracer(
            "volundr.events",
            tracer_provider=tracer_provider,
        )
        self._meter = metrics.get_meter(
            "volundr.events",
            meter_provider=meter_provider,
        )
        self._provider_name = provider_name
        self._service_name = service_name
        self._healthy = True

        # Metrics instruments (OTel GenAI conventions)
        self._token_histogram = self._meter.create_histogram(
            name="gen_ai.client.token.usage",
            description="Number of input and output tokens used",
            unit="{token}",
        )
        self._duration_histogram = self._meter.create_histogram(
            name="gen_ai.client.operation.duration",
            description="GenAI operation duration",
            unit="s",
        )

    # -- EventSink interface --------------------------------------------------

    async def emit(self, event: SessionEvent) -> None:
        self._emit_span(event)
        self._emit_metrics(event)

    async def emit_batch(self, events: list[SessionEvent]) -> None:
        for event in events:
            self._emit_span(event)
            self._emit_metrics(event)

    async def flush(self) -> None:
        # OTel SDK handles its own batching/export
        pass

    async def close(self) -> None:
        self._healthy = False

    @property
    def sink_name(self) -> str:
        return "otel"

    @property
    def healthy(self) -> bool:
        return self._healthy

    # -- Internal: spans ------------------------------------------------------

    def _emit_span(self, event: SessionEvent) -> None:
        from opentelemetry.trace import SpanKind, StatusCode

        operation = _EVENT_TO_OPERATION.get(event.event_type, "chat")
        model = event.model or "unknown"
        span_name = f"{operation} {model}"

        # Tool events get "execute_tool {tool_name}" naming
        if event.event_type in (
            SessionEventType.TOOL_USE,
            SessionEventType.FILE_CREATED,
            SessionEventType.FILE_MODIFIED,
            SessionEventType.FILE_DELETED,
            SessionEventType.TERMINAL_COMMAND,
        ):
            tool_name = self._extract_tool_name(event)
            span_name = f"execute_tool {tool_name}"

        # Agent lifecycle events
        if event.event_type in (
            SessionEventType.SESSION_START,
            SessionEventType.SESSION_STOP,
        ):
            agent_name = event.data.get("session_name", "skuld")
            span_name = f"invoke_agent {agent_name}"

        with self._tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CLIENT,
        ) as span:
            # Required attributes
            span.set_attribute(_ATTR_OPERATION_NAME, operation)
            span.set_attribute(_ATTR_PROVIDER_NAME, self._provider_name)

            # Model
            if event.model:
                span.set_attribute(_ATTR_REQUEST_MODEL, event.model)
                span.set_attribute(_ATTR_RESPONSE_MODEL, event.model)

            # Conversation (session) ID
            span.set_attribute(_ATTR_CONVERSATION_ID, str(event.session_id))

            # Token usage
            if event.tokens_in is not None:
                span.set_attribute(_ATTR_USAGE_INPUT_TOKENS, event.tokens_in)
            if event.tokens_out is not None:
                span.set_attribute(_ATTR_USAGE_OUTPUT_TOKENS, event.tokens_out)

            # Finish reason (for message events)
            finish_reason = event.data.get("finish_reason")
            if finish_reason:
                span.set_attribute(_ATTR_RESPONSE_FINISH_REASONS, [finish_reason])

            # Tool attributes
            if operation == "execute_tool":
                tool_name = self._extract_tool_name(event)
                span.set_attribute(_ATTR_TOOL_NAME, tool_name)
                span.set_attribute(_ATTR_TOOL_TYPE, "function")

            # Agent attributes
            if event.event_type in (
                SessionEventType.SESSION_START,
                SessionEventType.SESSION_STOP,
            ):
                span.set_attribute(
                    _ATTR_AGENT_NAME,
                    event.data.get("session_name", "skuld"),
                )
                span.set_attribute(_ATTR_AGENT_ID, str(event.session_id))

            # Duration
            if event.duration_ms is not None:
                span.set_attribute("gen_ai.operation.duration_ms", event.duration_ms)

            # Error events
            if event.event_type == SessionEventType.ERROR:
                span.set_status(StatusCode.ERROR, event.data.get("message", ""))
                span.set_attribute("error.type", event.data.get("source", "unknown"))

            # Custom attributes for data richness
            span.set_attribute("volundr.event_type", event.event_type.value)
            span.set_attribute("volundr.sequence", event.sequence)

    # -- Internal: metrics ----------------------------------------------------

    def _emit_metrics(self, event: SessionEvent) -> None:
        model = event.model or "unknown"
        operation = _EVENT_TO_OPERATION.get(event.event_type, "chat")

        base_attrs = {
            _ATTR_OPERATION_NAME: operation,
            _ATTR_PROVIDER_NAME: self._provider_name,
            _ATTR_REQUEST_MODEL: model,
        }

        # Token usage histogram (input + output as separate recordings)
        if event.tokens_in is not None and event.tokens_in > 0:
            self._token_histogram.record(
                event.tokens_in,
                attributes={**base_attrs, _ATTR_TOKEN_TYPE: "input"},
            )
        if event.tokens_out is not None and event.tokens_out > 0:
            self._token_histogram.record(
                event.tokens_out,
                attributes={**base_attrs, _ATTR_TOKEN_TYPE: "output"},
            )

        # Operation duration histogram (seconds)
        if event.duration_ms is not None:
            self._duration_histogram.record(
                event.duration_ms / 1000.0,
                attributes=base_attrs,
            )

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _extract_tool_name(event: SessionEvent) -> str:
        """Extract a meaningful tool name from event data."""
        match event.event_type:
            case SessionEventType.FILE_CREATED:
                return "file_write"
            case SessionEventType.FILE_MODIFIED:
                return "file_edit"
            case SessionEventType.FILE_DELETED:
                return "file_delete"
            case SessionEventType.GIT_COMMIT:
                return "git_commit"
            case SessionEventType.GIT_PUSH:
                return "git_push"
            case SessionEventType.GIT_BRANCH:
                return "git_branch"
            case SessionEventType.GIT_CHECKOUT:
                return "git_checkout"
            case SessionEventType.TERMINAL_COMMAND:
                return "bash"
            case SessionEventType.TOOL_USE:
                return event.data.get("tool", "unknown_tool")
            case _:
                return "unknown"
        raise AssertionError("Unreachable _extract_tool_name fallthrough")
