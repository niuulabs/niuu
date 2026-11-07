"""Tests for Ravn's optional OpenTelemetry facade."""

from __future__ import annotations

import logging

import pytest


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


def test_histogram_buckets_are_applied_when_given() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider

    from niuu.observability import Observability

    reader = InMemoryMetricReader()
    telemetry = Observability(
        tracer_provider=TracerProvider(),
        meter_provider=MeterProvider(metric_readers=[reader]),
    )
    telemetry.duration("fast.call", 0.2, buckets=[0.1, 0.25, 0.5])
    telemetry.record("unit.score", 0.7)

    points = {
        metric.name: metric.data.data_points[0]
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert list(points["fast.call"].explicit_bounds) == [0.1, 0.25, 0.5]
    assert list(points["fast.call"].bucket_counts) == [0, 1, 0, 0]
    assert list(points["unit.score"].explicit_bounds)[:3] == [0.0, 5.0, 10.0]

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


def test_log_record_factory_adds_active_trace_ids() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from niuu.observability import (
        Observability,
        disable_trace_log_correlation,
        enable_trace_log_correlation,
    )

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=MeterProvider(metric_readers=[InMemoryMetricReader()]),
    )
    logger = logging.getLogger("tests.observability")

    try:
        enable_trace_log_correlation()
        with telemetry.span("logged"):
            record = logger.makeRecord(
                logger.name,
                logging.INFO,
                __file__,
                1,
                "build started",
                (),
                None,
            )
    finally:
        disable_trace_log_correlation()
        telemetry.shutdown()

    span = exporter.get_finished_spans()[0]
    assert record.trace_id == f"{span.context.trace_id:032x}"
    assert record.span_id == f"{span.context.span_id:016x}"
    assert record.otelTraceID == record.trace_id
    assert record.otelSpanID == record.span_id


class TestMetricToolNameBounding:
    """A garbled tool call must not mint a new metric time series.

    Observed in production: tool-name labels containing a full JSON tool
    definition, a stray ``</parameter=1``, and a bare first name — one series
    each, permanently, in the tool-usage panels.
    """

    def test_real_tool_names_pass_through(self) -> None:
        from ravn.tool_observability import metric_tool_name

        for name in (
            "kubernetes_inspect",
            "mimir_search",
            "learned_tool_run",
            "web.fetch",
            "a2a:send",
            "build-tool",
        ):
            assert metric_tool_name(name) == name

    def test_malformed_names_collapse_to_one_label(self) -> None:
        from ravn.tool_observability import MALFORMED_TOOL_LABEL, metric_tool_name

        malformed = [
            '{\n  "name": "valkyrie-inspect-kubernetes-node",\n  "description": "..."\n}',
            "</parameter=1",
            "Elliott Smith wrote it",
            "kube-apiserver-jarnvidr-controlplane-xwdpt-jnk22\n</parameter=1",
            "x" * 200,
            "",
            None,
        ]
        labels = {metric_tool_name(n) for n in malformed}
        assert labels == {MALFORMED_TOOL_LABEL}

    def test_whitespace_is_not_a_new_series(self) -> None:
        from ravn.tool_observability import metric_tool_name

        assert metric_tool_name("  mimir_search  ") == "mimir_search"
