"""Mesh transports carry an event's W3C trace context, and refuse a malformed event.

The nng encoding (``_serialization``) and the webhook envelope both dropped
``MeshEvent.trace_context``, so a receiver's spans started a new trace; the webhook
decoder also read an unknown event type as ``response``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.adapters.mesh._serialization import decode_event, encode_event
from niuu.adapters.mesh.webhook import WebhookMeshAdapter
from niuu.mesh.events import MeshEvent, MeshEventType

TRACE = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}


def _event() -> MeshEvent:
    return MeshEvent(
        type=MeshEventType.RESPONSE,
        source="skuld:peer-a",
        payload={"text": "done"},
        timestamp=datetime.now(UTC),
        urgency=0.5,
        correlation_id="corr-1",
        session_id="sess-1",
        trace_context=dict(TRACE),
    )


def test_shared_encoding_round_trips_trace_context() -> None:
    assert decode_event(encode_event(_event())).trace_context == TRACE


def test_webhook_envelope_round_trips_trace_context() -> None:
    adapter = WebhookMeshAdapter(own_peer_id="peer-a", discovery=None)
    wire = adapter._event_to_dict(_event(), "topic.a", "publish")
    assert adapter._dict_to_event(wire).trace_context == TRACE


def test_webhook_refuses_an_unknown_event_type() -> None:
    adapter = WebhookMeshAdapter(own_peer_id="peer-a", discovery=None)
    wire = adapter._event_to_dict(_event(), "topic.a", "publish")
    wire["event"]["type"] = "not-a-mesh-event-type"
    with pytest.raises(ValueError):
        adapter._dict_to_event(wire)


def test_webhook_refuses_an_event_missing_fields() -> None:
    adapter = WebhookMeshAdapter(own_peer_id="peer-a", discovery=None)
    with pytest.raises(KeyError):
        adapter._dict_to_event({"type": "publish", "topic": "t", "event": {"type": "response"}})
