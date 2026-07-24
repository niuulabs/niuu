"""Tests for Ravn's optional OpenTelemetry facade."""

from __future__ import annotations

import pytest

from ravn.adapters.tools.build_tool import _build_result_outcome
from ravn.domain.models import ToolResult


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (ToolResult(tool_call_id="", content="failed", is_error=True), "error"),
        (
            ToolResult(tool_call_id="", content='{"registered": true}'),
            "registered",
        ),
        (
            ToolResult(
                tool_call_id="",
                content='{"review_required": true, "review_filed": true}',
            ),
            "review_pending",
        ),
        (
            ToolResult(tool_call_id="", content='{"status": "input_required"}'),
            "input_required",
        ),
    ],
)
def test_tool_build_lifecycle_outcome_is_not_inferred_from_success_alone(
    result: ToolResult,
    expected: str,
) -> None:
    assert _build_result_outcome(result) == expected


def test_span_context_and_metrics_are_real_when_sdk_is_installed() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from niuu.observability import Observability

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        capture_content=True,
        content_max_chars=256,
    )

    with telemetry.span("signal", attributes={"ravn.environment.id": "workshop"}):
        carrier = telemetry.inject()
    with telemetry.span("agent", carrier=carrier):
        telemetry.event(
            "model.response",
            content={"answer": "inspect capabilities", "token": "must-not-escape"},
        )
        assert len(telemetry.trace_id()) == 32
        telemetry.count("ravn.agent.tasks", attributes={"ravn.task.outcome": "complete"})
        telemetry.duration("ravn.agent.task.duration", 0.25)
        telemetry.gauge("ravn.queue.depth", 3)
    with telemetry.span("failed") as failed_span:
        telemetry.mark_error(
            failed_span,
            "a2a_rpc_failed",
            "A2A SendMessage failed with Bearer secret-value",
        )

    spans = span_exporter.get_finished_spans()
    assert [span.name for span in spans] == ["signal", "agent", "failed"]
    assert spans[0].context.trace_id == spans[1].context.trace_id
    assert spans[1].events[0].name == "model.response"
    assert "must-not-escape" not in spans[1].events[0].attributes["ravn.content"]
    assert "[REDACTED]" in spans[1].events[0].attributes["ravn.content"]
    assert carrier["traceparent"].startswith("00-")
    assert spans[2].attributes["error.type"] == "a2a_rpc_failed"
    assert spans[2].attributes["error.message"] == ("A2A SendMessage failed with Bearer [REDACTED]")
    assert spans[2].status.description == "A2A SendMessage failed with Bearer [REDACTED]"
    metric_names = {
        metric.name
        for resource in metric_reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert metric_names == {
        "ravn.agent.tasks",
        "ravn.agent.task.duration",
        "ravn.queue.depth",
    }

    telemetry.shutdown()


def test_linked_span_starts_a_bounded_trace_with_causal_link() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from niuu.observability import Observability

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=MeterProvider(metric_readers=[InMemoryMetricReader()]),
    )

    with telemetry.span("before-restart"):
        carrier = telemetry.inject()
    with telemetry.span("after-restart", link_carrier=carrier):
        pass

    before, after = exporter.get_finished_spans()
    assert before.context.trace_id != after.context.trace_id
    assert len(after.links) == 1
    assert after.links[0].context.trace_id == before.context.trace_id
    telemetry.shutdown()
