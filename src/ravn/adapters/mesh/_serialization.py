"""Shared RavnEvent JSON serialization for mesh adapters (NIU-517).

Both NngMeshAdapter and SleipnirMeshAdapter encode/decode RavnEvent objects
identically — only the framing differs (nng prepends a topic prefix, Sleipnir
uses raw bytes).  This module owns the common event ↔ dict ↔ bytes conversion
so neither adapter duplicates it.
"""

from __future__ import annotations

import json
from datetime import datetime

from ravn.domain.events import RavnEvent, RavnEventType


def event_to_dict(event: RavnEvent) -> dict:
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
    }


def dict_to_event(raw: dict) -> RavnEvent:
    """Reconstruct a :class:`RavnEvent` from a decoded JSON dict."""
    return RavnEvent(
        type=RavnEventType(raw["type"]),
        source=raw["source"],
        payload=raw["payload"],
        timestamp=datetime.fromisoformat(raw["timestamp"]),
        urgency=raw["urgency"],
        correlation_id=raw["correlation_id"],
        session_id=raw["session_id"],
        task_id=raw.get("task_id"),
        root_correlation_id=raw.get("root_correlation_id", ""),
    )


def encode_event(event: RavnEvent) -> bytes:
    """Serialise *event* to UTF-8 JSON bytes."""
    return json.dumps(event_to_dict(event)).encode()


def decode_event(data: bytes) -> RavnEvent:
    """Deserialise a :class:`RavnEvent` from UTF-8 JSON bytes."""
    return dict_to_event(json.loads(data))
