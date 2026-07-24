"""Tests for the SleipnirEvent model and event type registry."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import pytest

from sleipnir.domain.events import (
    EVENT_NAMESPACES,
    KNOWN_DOMAINS,
    SleipnirEvent,
    match_event_type,
    validate_event_type,
)
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_event_construction_minimal():
    evt = make_event()
    assert evt.event_id == "evt-001"
    assert evt.event_type == "ravn.tool.complete"
    assert evt.source == "ravn:agent-abc123"
    assert evt.urgency == 0.5
    assert evt.correlation_id is None
    assert evt.causation_id is None
    assert evt.tenant_id is None
    assert evt.ttl is None


def test_event_construction_full():
    evt = make_event(
        correlation_id="corr-123",
        causation_id="cause-456",
        tenant_id="tenant-789",
        ttl=300,
    )
    assert evt.correlation_id == "corr-123"
    assert evt.causation_id == "cause-456"
    assert evt.tenant_id == "tenant-789"
    assert evt.ttl == 300


# ---------------------------------------------------------------------------
# event_id auto-generation
# ---------------------------------------------------------------------------


def test_event_id_auto_generated_when_not_supplied():
    evt = make_event(event_id=None)
    assert evt.event_id is not None
    # Must be a valid UUID4 string
    parsed = uuid.UUID(evt.event_id, version=4)
    assert str(parsed) == evt.event_id


def test_event_id_auto_generated_is_unique():
    evt_a = make_event(event_id=None)
    evt_b = make_event(event_id=None)
    assert evt_a.event_id != evt_b.event_id


def test_event_id_explicit_value_preserved():
    evt = make_event(event_id="my-custom-id")
    assert evt.event_id == "my-custom-id"


# ---------------------------------------------------------------------------
# urgency validation
# ---------------------------------------------------------------------------


def test_event_urgency_boundaries():
    lo = make_event(urgency=0.0)
    hi = make_event(urgency=1.0)
    assert lo.urgency == 0.0
    assert hi.urgency == 1.0


def test_event_urgency_invalid_low():
    with pytest.raises(ValueError, match="urgency"):
        make_event(urgency=-0.1)


def test_event_urgency_invalid_high():
    with pytest.raises(ValueError, match="urgency"):
        make_event(urgency=1.1)


# ---------------------------------------------------------------------------
# domain validation
# ---------------------------------------------------------------------------


def test_event_known_domains_accepted():
    for domain in KNOWN_DOMAINS:
        evt = make_event(domain=domain)
        assert evt.domain == domain


def test_event_unknown_domain_rejected():
    with pytest.raises(ValueError, match="Unknown domain"):
        make_event(domain="finance")


def test_event_unknown_domain_error_lists_known_domains():
    with pytest.raises(ValueError) as exc_info:
        make_event(domain="unknown_domain")
    assert "business" in str(exc_info.value)


# ---------------------------------------------------------------------------
# event_type validation
# ---------------------------------------------------------------------------


def test_event_invalid_event_type_rejected():
    with pytest.raises(ValueError):
        make_event(event_type="INVALID")


def test_event_unknown_namespace_emits_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="sleipnir.domain.events"):
        evt = make_event(event_type="external.tool.call")
    assert evt.event_type == "external.tool.call"
    assert any("external" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Serialisation round-trip (JSON path, no msgpack needed)
# ---------------------------------------------------------------------------


def test_to_dict_contains_all_fields():
    evt = make_event(
        correlation_id="corr-1",
        tenant_id="t-1",
        ttl=60,
        trace_context={"traceparent": "00-abc-def-01"},
    )
    d = evt.to_dict()
    assert d["event_id"] == "evt-001"
    assert d["event_type"] == "ravn.tool.complete"
    assert d["source"] == "ravn:agent-abc123"
    assert d["summary"] == "Bash tool completed successfully"
    assert d["urgency"] == 0.5
    assert d["domain"] == "code"
    assert d["correlation_id"] == "corr-1"
    assert d["causation_id"] is None
    assert d["tenant_id"] == "t-1"
    assert d["ttl"] == 60
    assert d["trace_context"] == {"traceparent": "00-abc-def-01"}
    assert isinstance(d["timestamp"], str)


def test_round_trip_from_dict():
    original = make_event(
        correlation_id="c1",
        causation_id="c2",
        tenant_id="t1",
        ttl=120,
        trace_context={"traceparent": "00-abc-def-01"},
    )
    restored = SleipnirEvent.from_dict(original.to_dict())
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
    assert restored.trace_context == original.trace_context


def test_from_dict_parses_iso_timestamp():
    d = make_event().to_dict()
    assert isinstance(d["timestamp"], str)
    restored = SleipnirEvent.from_dict(d)
    assert isinstance(restored.timestamp, datetime)


def test_from_dict_accepts_datetime_object():
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    d = make_event().to_dict()
    d["timestamp"] = ts  # already a datetime
    restored = SleipnirEvent.from_dict(d)
    assert restored.timestamp == ts


def test_now_returns_utc_datetime():
    ts = SleipnirEvent.now()
    assert ts.tzinfo is not None


# ---------------------------------------------------------------------------
# Validate event type
# ---------------------------------------------------------------------------


def test_validate_known_namespaces():
    for ns in EVENT_NAMESPACES:
        validate_event_type(f"{ns}.something")
        validate_event_type(f"{ns}.a.b.c")


def test_validate_unknown_namespace_emits_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="sleipnir.domain.events"):
        validate_event_type("unknown.event.type")
    assert any("unknown" in r.message for r in caplog.records)


def test_validate_unknown_namespace_does_not_raise():
    # Unknown namespaces are warned about but not rejected (extensibility)
    validate_event_type("newservice.event.fired")  # must not raise


def test_validate_single_segment():
    with pytest.raises(ValueError, match="Invalid event type"):
        validate_event_type("ravn")


def test_validate_uppercase_rejected():
    with pytest.raises(ValueError, match="Invalid event type"):
        validate_event_type("Ravn.tool.complete")


def test_validate_empty_string():
    with pytest.raises(ValueError, match="Invalid event type"):
        validate_event_type("")


def test_validate_numeric_first_char():
    with pytest.raises(ValueError, match="Invalid event type"):
        validate_event_type("1ravn.tool")


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,event_type,expected",
    [
        # Wildcard namespace match
        ("ravn.*", "ravn.tool.complete", True),
        ("ravn.*", "ravn.step.start", True),
        ("ravn.*", "ting.saga.created", False),
        # Sub-namespace wildcard
        ("ravn.tool.*", "ravn.tool.complete", True),
        ("ravn.tool.*", "ravn.tool.start", True),
        ("ravn.tool.*", "ravn.step.start", False),
        # Exact match
        ("ravn.tool.complete", "ravn.tool.complete", True),
        ("ravn.tool.complete", "ravn.tool.start", False),
        # Global wildcard
        ("*", "ravn.tool.complete", True),
        ("*", "system.health.ping", True),
        # No match across namespaces
        ("ting.*", "ravn.tool.complete", False),
        ("volundr.*", "bifrost.connection.open", False),
    ],
)
def test_match_event_type(pattern: str, event_type: str, expected: bool):
    assert match_event_type(pattern, event_type) is expected
