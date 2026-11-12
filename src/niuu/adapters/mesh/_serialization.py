"""Shared MeshEvent JSON serialization for mesh adapters (NIU-517).

NngMeshAdapter and WebhookMeshAdapter encode/decode MeshEvent objects with
these helpers — only the framing differs (nng prepends a topic prefix, the
webhook wraps the event in a publish envelope). The W3C trace context travels
with the event, so a receiver's spans join the sender's trace. This module owns
the common event ↔ dict ↔ bytes conversion so neither adapter duplicates it.
"""

from __future__ import annotations

import json
from datetime import datetime

from niuu.mesh.events import MeshEvent, MeshEventType


def event_to_dict(event: MeshEvent) -> dict:
    """Return a JSON-serialisable dict representation of *event*."""
    return {
        "type": event.type,
        "source": event.source,
        "payload": event.payload,
        "timestamp": event.timestamp.isoformat(),
        "urgency": event.urgency,
        "correlation_id": event.correlation_id,
        "session_id": event.session_id,
        "task_id": event.task_id,
        "root_correlation_id": event.root_correlation_id,
        "trace_context": dict(event.trace_context),
        "event_id": event.event_id,
        "attestation": event.attestation,
    }


def dict_to_event(raw: dict) -> MeshEvent:
    """Reconstruct a :class:`MeshEvent` from a decoded JSON dict."""
    return MeshEvent(
        type=MeshEventType(raw["type"]),
        source=raw["source"],
        payload=raw["payload"],
        timestamp=datetime.fromisoformat(raw["timestamp"]),
        urgency=raw["urgency"],
        correlation_id=raw["correlation_id"],
        session_id=raw["session_id"],
        task_id=raw.get("task_id"),
        root_correlation_id=raw.get("root_correlation_id", ""),
        trace_context=dict(raw.get("trace_context") or {}),
        **({"event_id": raw["event_id"]} if raw.get("event_id") else {}),
        attestation=dict(raw.get("attestation") or {}),
    )


def encode_event(event: MeshEvent) -> bytes:
    """Serialise *event* to UTF-8 JSON bytes."""
    return json.dumps(event_to_dict(event)).encode()


def decode_event(data: bytes) -> MeshEvent:
    """Deserialise a :class:`MeshEvent` from UTF-8 JSON bytes."""
    return dict_to_event(json.loads(data))
