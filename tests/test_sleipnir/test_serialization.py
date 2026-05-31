"""Tests for Sleipnir serialisation helpers."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from sleipnir.adapters.serialization import (
    deserialize,
    msgpack_available,
    serialize,
)
from tests.test_sleipnir.conftest import DEFAULT_TIMESTAMP, make_event

_skip_no_msgpack = pytest.mark.skipif(
    not msgpack_available(),
    reason="msgpack not installed",
)


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


def test_json_serialize_returns_bytes():
    data = serialize(make_event(), fmt="json")
    assert isinstance(data, bytes)


def test_json_round_trip():
    original = make_event()
    data = serialize(original, fmt="json")
    restored = deserialize(data, fmt="json")

    assert restored.event_id == original.event_id
    assert restored.event_type == original.event_type
    assert restored.source == original.source
    assert restored.payload == original.payload
    assert restored.summary == original.summary
    assert restored.urgency == original.urgency
    assert restored.domain == original.domain
    assert restored.correlation_id == original.correlation_id
    assert restored.causation_id == original.causation_id
    assert restored.tenant_id == original.tenant_id
    assert restored.ttl == original.ttl


def test_json_round_trip_none_fields():
    original = make_event(correlation_id=None, causation_id=None, tenant_id=None, ttl=None)
    data = serialize(original, fmt="json")
    restored = deserialize(data, fmt="json")

    assert restored.correlation_id is None
    assert restored.causation_id is None
    assert restored.tenant_id is None
    assert restored.ttl is None


def test_json_round_trip_preserves_timestamp():
    original = make_event()
    data = serialize(original, fmt="json")
    restored = deserialize(data, fmt="json")
    assert restored.timestamp.isoformat() == DEFAULT_TIMESTAMP.isoformat()


def test_json_round_trip_complex_payload():
    original = make_event(payload={"nested": {"a": 1, "b": [1, 2, 3]}, "flag": True})
    data = serialize(original, fmt="json")
    restored = deserialize(data, fmt="json")
    assert restored.payload == original.payload


# ---------------------------------------------------------------------------
# Unknown format
# ---------------------------------------------------------------------------


def test_serialize_unknown_format_raises():
    with pytest.raises(ValueError, match="Unknown serialisation format"):
        serialize(make_event(), fmt="xml")  # type: ignore[arg-type]


def test_deserialize_unknown_format_raises():
    with pytest.raises(ValueError, match="Unknown serialisation format"):
        deserialize(b"data", fmt="xml")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# msgpack unavailable path (always tested via mock)
# ---------------------------------------------------------------------------


def test_msgpack_available_returns_bool():
    assert isinstance(msgpack_available(), bool)


def test_serialize_msgpack_raises_import_error_when_unavailable():
    with patch.object(sys.modules["sleipnir.adapters.serialization"], "_MSGPACK_AVAILABLE", False):
        with pytest.raises(ImportError, match="msgpack is not installed"):
            serialize(make_event(), fmt="msgpack")


def test_deserialize_msgpack_raises_import_error_when_unavailable():
    with patch.object(sys.modules["sleipnir.adapters.serialization"], "_MSGPACK_AVAILABLE", False):
        with pytest.raises(ImportError, match="msgpack is not installed"):
            deserialize(b"\x80", fmt="msgpack")


# ---------------------------------------------------------------------------
# msgpack serialisation (skipped if not installed)
# ---------------------------------------------------------------------------


@_skip_no_msgpack
def test_msgpack_available_returns_true_when_installed():
    assert msgpack_available() is True


@_skip_no_msgpack
def test_msgpack_serialize_returns_bytes():
    data = serialize(make_event(), fmt="msgpack")
    assert isinstance(data, bytes)


@_skip_no_msgpack
def test_msgpack_round_trip():
    original = make_event()
    data = serialize(original, fmt="msgpack")
    restored = deserialize(data, fmt="msgpack")

    assert restored.event_id == original.event_id
    assert restored.event_type == original.event_type
    assert restored.source == original.source
    assert restored.payload == original.payload
    assert restored.summary == original.summary
    assert restored.urgency == original.urgency
    assert restored.domain == original.domain
    assert restored.correlation_id == original.correlation_id
    assert restored.causation_id == original.causation_id
    assert restored.tenant_id == original.tenant_id
    assert restored.ttl == original.ttl


@_skip_no_msgpack
def test_msgpack_round_trip_none_fields():
    original = make_event(correlation_id=None, causation_id=None, tenant_id=None, ttl=None)
    data = serialize(original, fmt="msgpack")
    restored = deserialize(data, fmt="msgpack")

    assert restored.correlation_id is None
    assert restored.causation_id is None
    assert restored.tenant_id is None
    assert restored.ttl is None


@_skip_no_msgpack
def test_msgpack_is_more_compact_than_json():
    evt = make_event()
    msgpack_bytes = serialize(evt, fmt="msgpack")
    json_bytes = serialize(evt, fmt="json")
    assert len(msgpack_bytes) < len(json_bytes)


# ---------------------------------------------------------------------------
# NIU-522: msgpack serialisation correctness
# ---------------------------------------------------------------------------


@_skip_no_msgpack
def test_msgpack_timestamp_serialized_as_iso_string():
    """Timestamp must be an ISO 8601 string in the wire dict, not a datetime object.

    msgpack cannot encode datetime natively; the wire format uses to_dict()
    which serialises timestamp as isoformat().  Verify the intermediate
    representation is a plain string so it survives any msgpack round-trip.
    """
    evt = make_event()
    wire_dict = evt.to_dict()
    assert isinstance(wire_dict["timestamp"], str), (
        f"Expected str, got {type(wire_dict['timestamp'])}"
    )
    # Verify the string is valid ISO 8601 and round-trips to the same value.
    from datetime import datetime

    parsed = datetime.fromisoformat(wire_dict["timestamp"])
    assert parsed == evt.timestamp


@_skip_no_msgpack
def test_msgpack_urgency_float_precision_preserved():
    """High-precision float urgency must survive msgpack round-trip without loss."""
    high_precision = 0.123456789
    evt = make_event(urgency=high_precision)
    data = serialize(evt, fmt="msgpack")
    restored = deserialize(data, fmt="msgpack")
    # IEEE-754 double precision (64-bit) preserves at least 15 significant digits;
    # msgpack uses float64 by default so there should be zero loss.
    assert restored.urgency == pytest.approx(high_precision, rel=1e-9)


@_skip_no_msgpack
def test_msgpack_payload_extra_fields_preserved_forward_compatibility():
    """Unknown extra keys in payload survive msgpack round-trip unchanged.

    This validates forward compatibility: a payload produced by a newer version
    of the service with additional fields must not be silently dropped when
    consumed by an older version that only reads known keys.
    """
    payload = {
        "known_key": "value",
        "future_field": 42,
        "nested_future": {"x": True, "y": [1, 2, 3]},
        "none_future": None,
    }
    evt = make_event(payload=payload)
    data = serialize(evt, fmt="msgpack")
    restored = deserialize(data, fmt="msgpack")
    assert restored.payload["known_key"] == "value"
    assert restored.payload["future_field"] == 42
    assert restored.payload["nested_future"] == {"x": True, "y": [1, 2, 3]}
    assert restored.payload["none_future"] is None


@_skip_no_msgpack
def test_msgpack_typical_ravn_tool_complete_event_under_512_bytes():
    """A realistic ravn.tool.complete event must serialise to < 512 bytes.

    Architecture target: keep single-hop IPC messages well below 1 KB so that
    all fields fit in a single page-aligned nng buffer with room to spare.
    """
    evt = make_event(
        event_type="ravn.tool.complete",
        source="ravn:agent-abc123",
        payload={"tool": "bash", "exit_code": 0, "stdout": "ok", "stderr": ""},
        summary="Bash tool completed successfully",
        urgency=0.5,
        domain="code",
        correlation_id="corr-abc123",
    )
    data = serialize(evt, fmt="msgpack")
    assert len(data) < 512, (
        f"Serialised event is {len(data)} bytes — exceeds 512-byte target. "
        "Review payload size or field encoding."
    )
