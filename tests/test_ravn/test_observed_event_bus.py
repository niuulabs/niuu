"""Tests for causal trace propagation across the Sleipnir port decorator."""

from __future__ import annotations

import pytest

from niuu.observability import Observability
from ravn.adapters.observability import ObservedSleipnirBus
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent


@pytest.mark.asyncio
async def test_publish_and_consume_share_trace_context(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=MeterProvider(metric_readers=[metric_reader]),
        capture_content=True,
    )
    monkeypatch.setattr("ravn.adapters.observability.get_observability", lambda: telemetry)

    bus = ObservedSleipnirBus(InProcessBus())
    received: list[SleipnirEvent] = []

    async def receive(event: SleipnirEvent) -> None:
        received.append(event)

    subscription = await bus.subscribe(["signal.*"], receive)
    event = SleipnirEvent(
        event_type="signal.received",
        source="test",
        payload={"state": "changed"},
        summary="state changed",
        urgency=0.2,
        domain="infrastructure",
        timestamp=SleipnirEvent.now(),
    )

    with telemetry.span("root"):
        await bus.publish(event)
    await bus.flush()

    assert received == [event]
    assert event.trace_context["traceparent"].startswith("00-")
    spans = span_exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "root",
        "publish signal.received",
        "process signal.received",
    }
    assert len({span.context.trace_id for span in spans}) == 1
    metrics = metric_reader.get_metrics_data()
    operation_metric = next(
        metric
        for resource in metrics.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "ravn.event.bus.operations"
    )
    for point in operation_metric.data.data_points:
        assert "messaging.message.id" not in point.attributes
        assert "ravn.correlation.id" not in point.attributes
        assert "ravn.causation.id" not in point.attributes
        assert "messaging.batch.message_count" not in point.attributes

    await subscription.unsubscribe()
    telemetry.shutdown()
