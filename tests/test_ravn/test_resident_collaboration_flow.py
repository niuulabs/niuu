"""Resident collaboration proof across Ravn projection and Skuld delivery."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.channels.skuld_channel import SkuldChannel
from ravn.domain.events import RavnEvent
from skuld.collaboration_adapter import SkuldCollaborationAdapter
from skuld.config import RoomConfig


@pytest.mark.asyncio
async def test_help_trace_and_exact_case_context_round_trip_to_operator_reply() -> None:
    channel = SkuldChannel(
        broker_url="ws://skuld/ws/ravn/resident-1",
        session_id="resident-session",
        peer_id="resident-1",
        persona="Resident",
    )
    help_event = RavnEvent.help_needed(
        source="resident-1",
        persona="Resident",
        reason="operator_authority_required",
        summary="The action requires operator approval",
        attempted=["researched the signal", "verified the affected resource"],
        recommendation="Approve or deny the proposed action",
        correlation_id="case-1",
        session_id="resident-session",
        context={"resident_case_id": "case-1", "proposed_action": "pause-print"},
        trace_context={"traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"},
    )
    frame = json.loads(channel._serialise(help_event))

    channels = MagicMock()
    channels.broadcast = AsyncMock()
    room = SkuldCollaborationAdapter(
        RoomConfig(enabled=True, presence_sweep_interval_s=0), channels
    )
    resident_socket = MagicMock()
    resident_socket.send_text = AsyncMock()
    await room.register("resident-1", "Resident", resident_socket)
    channels.broadcast.reset_mock()

    await room.handle_collaboration_frame("resident-1", frame)

    notification = channels.broadcast.await_args.args[0]
    assert notification["notificationType"] == "help_needed"
    assert notification["trace_context"] == help_event.trace_context
    assert room.pending_help_peer_ids() == ("resident-1",)

    assert await room.route_directed_message(
        "resident-1",
        "Approved",
        metadata={"operator_id": "human:jozef"},
    )
    reply = json.loads(resident_socket.send_text.await_args.args[0])
    assert reply["content"] == "Approved"
    assert reply["metadata"]["help_context"] == {
        "resident_case_id": "case-1",
        "proposed_action": "pause-print",
    }
    assert reply["metadata"]["trace_context"] == help_event.trace_context
    assert reply["metadata"]["operator_id"] == "human:jozef"


@pytest.mark.asyncio
async def test_resident_help_and_operator_delivery_share_one_trace(monkeypatch) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from niuu.observability import Observability

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=MeterProvider(metric_readers=[InMemoryMetricReader()]),
    )
    monkeypatch.setattr("skuld.collaboration_adapter.get_observability", lambda: telemetry)

    channel = SkuldChannel(
        broker_url="ws://skuld/ws/ravn/resident-1",
        session_id="resident-session",
        peer_id="resident-1",
        persona="Resident",
    )
    with telemetry.span("ravn.judgment.help_needed"):
        help_event = RavnEvent.help_needed(
            source="resident-1",
            persona="Resident",
            reason="operator_authority_required",
            summary="Approval required",
            attempted=["verified the affected resource"],
            recommendation="Approve or deny",
            correlation_id="case-1",
            session_id="resident-session",
            context={"resident_case_id": "case-1"},
            trace_context=telemetry.inject(),
        )
        frame = json.loads(channel._serialise(help_event, trace_context=telemetry.inject()))

    channels = MagicMock()
    channels.broadcast = AsyncMock()
    room = SkuldCollaborationAdapter(
        RoomConfig(enabled=True, presence_sweep_interval_s=0), channels
    )
    resident_socket = MagicMock()
    resident_socket.send_text = AsyncMock()
    await room.register("resident-1", "Resident", resident_socket)
    await room.handle_collaboration_frame("resident-1", frame)
    assert await room.route_directed_message("resident-1", "Approved")

    spans = span_exporter.get_finished_spans()
    names = {span.name for span in spans}
    assert {
        "ravn.judgment.help_needed",
        "skuld.collaboration.event.receive",
        "skuld.collaboration.operator_attention",
        "skuld.collaboration.directed_message.deliver",
    } <= names
    assert len({span.context.trace_id for span in spans}) == 1
    telemetry.shutdown()
