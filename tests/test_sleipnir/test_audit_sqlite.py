"""Tests for SqliteAuditRepository — schema, CRUD, query, TTL purge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.audit import AuditQuery
from tests.test_sleipnir.conftest import DEFAULT_TIMESTAMP, make_event

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def repo() -> SqliteAuditRepository:
    """Fresh in-memory SQLite repository per test."""
    r = SqliteAuditRepository(":memory:")
    yield r
    await r.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(offset_seconds: int = 0) -> datetime:
    return DEFAULT_TIMESTAMP + timedelta(seconds=offset_seconds)


# ---------------------------------------------------------------------------
# append + query basics
# ---------------------------------------------------------------------------


async def test_append_and_retrieve(repo):
    evt = make_event()
    await repo.append(evt)

    results = await repo.query(AuditQuery())
    assert len(results) == 1
    assert results[0].event_id == evt.event_id
    assert results[0].event_type == evt.event_type
    assert results[0].source == evt.source


async def test_append_is_idempotent(repo):
    """Inserting the same event twice must not raise or duplicate rows."""
    evt = make_event()
    await repo.append(evt)
    await repo.append(evt)

    results = await repo.query(AuditQuery())
    assert len(results) == 1


async def test_payload_roundtrip(repo):
    evt = make_event(payload={"key": "value", "nested": {"a": 1}})
    await repo.append(evt)

    results = await repo.query(AuditQuery())
    assert results[0].payload == {"key": "value", "nested": {"a": 1}}


async def test_nullable_fields_roundtrip(repo):
    evt = make_event(correlation_id=None, causation_id=None, tenant_id=None, ttl=None)
    await repo.append(evt)

    results = await repo.query(AuditQuery())
    r = results[0]
    assert r.correlation_id is None
    assert r.causation_id is None
    assert r.tenant_id is None
    assert r.ttl is None


async def test_optional_fields_roundtrip(repo):
    evt = make_event(
        correlation_id="corr-1",
        causation_id="cause-1",
        tenant_id="tenant-1",
        ttl=300,
    )
    await repo.append(evt)

    results = await repo.query(AuditQuery())
    r = results[0]
    assert r.correlation_id == "corr-1"
    assert r.causation_id == "cause-1"
    assert r.tenant_id == "tenant-1"
    assert r.ttl == 300


# ---------------------------------------------------------------------------
# Query filtering
# ---------------------------------------------------------------------------


async def test_query_no_filters_returns_all(repo):
    events = [
        make_event(event_id="a", event_type="ravn.tool.complete", timestamp=_ts(0)),
        make_event(event_id="b", event_type="ting.task.started", timestamp=_ts(1)),
        make_event(event_id="c", event_type="volundr.session.started", timestamp=_ts(2)),
    ]
    for e in events:
        await repo.append(e)

    results = await repo.query(AuditQuery())
    assert len(results) == 3


async def test_query_event_type_pattern_exact(repo):
    await repo.append(make_event(event_id="a", event_type="ravn.tool.complete"))
    await repo.append(make_event(event_id="b", event_type="ting.task.started"))

    results = await repo.query(AuditQuery(event_type_pattern="ravn.tool.complete"))
    assert len(results) == 1
    assert results[0].event_id == "a"


async def test_query_event_type_pattern_glob(repo):
    await repo.append(make_event(event_id="a", event_type="ravn.tool.complete"))
    await repo.append(make_event(event_id="b", event_type="ravn.step.start"))
    await repo.append(make_event(event_id="c", event_type="ting.task.started"))

    results = await repo.query(AuditQuery(event_type_pattern="ravn.*"))
    ids = {r.event_id for r in results}
    assert ids == {"a", "b"}


async def test_query_wildcard_matches_all(repo):
    await repo.append(make_event(event_id="a", event_type="ravn.tool.complete"))
    await repo.append(make_event(event_id="b", event_type="ting.task.started"))

    results = await repo.query(AuditQuery(event_type_pattern="*"))
    assert len(results) == 2


async def test_query_from_ts_filter(repo):
    await repo.append(make_event(event_id="old", timestamp=_ts(-100)))
    await repo.append(make_event(event_id="new", timestamp=_ts(100)))

    results = await repo.query(AuditQuery(from_ts=_ts(0)))
    assert len(results) == 1
    assert results[0].event_id == "new"


async def test_query_to_ts_filter(repo):
    await repo.append(make_event(event_id="old", timestamp=_ts(-100)))
    await repo.append(make_event(event_id="new", timestamp=_ts(100)))

    results = await repo.query(AuditQuery(to_ts=_ts(0)))
    assert len(results) == 1
    assert results[0].event_id == "old"


async def test_query_correlation_id_filter(repo):
    await repo.append(make_event(event_id="a", correlation_id="corr-A"))
    await repo.append(make_event(event_id="b", correlation_id="corr-B"))

    results = await repo.query(AuditQuery(correlation_id="corr-A"))
    assert len(results) == 1
    assert results[0].event_id == "a"


async def test_query_source_filter(repo):
    await repo.append(make_event(event_id="a", source="ravn:agent-1"))
    await repo.append(make_event(event_id="b", source="ting:dispatcher"))

    results = await repo.query(AuditQuery(source="ravn:agent-1"))
    assert len(results) == 1
    assert results[0].event_id == "a"


async def test_query_limit_respected(repo):
    for i in range(10):
        await repo.append(make_event(event_id=f"evt-{i}"))

    results = await repo.query(AuditQuery(limit=3))
    assert len(results) == 3


async def test_query_results_are_newest_first(repo):
    await repo.append(make_event(event_id="old", timestamp=_ts(-60)))
    await repo.append(make_event(event_id="new", timestamp=_ts(60)))

    results = await repo.query(AuditQuery())
    assert results[0].event_id == "new"
    assert results[1].event_id == "old"


# ---------------------------------------------------------------------------
# TTL purge
# ---------------------------------------------------------------------------


async def test_purge_expired_removes_ttl_events(repo):
    """Events with an elapsed TTL should be deleted."""
    past = datetime.now(UTC) - timedelta(hours=2)
    expired = SleipnirEvent(
        event_id="expired",
        event_type="ravn.tool.complete",
        source="ravn:agent",
        payload={},
        summary="old event",
        urgency=0.3,
        domain="code",
        timestamp=past,
        ttl=1,  # 1 second TTL — already elapsed since timestamp is 2 hours ago
    )
    await repo.append(expired)

    # Also insert a non-expired event
    await repo.append(make_event(event_id="fresh"))

    deleted = await repo.purge_expired()
    assert deleted == 1

    results = await repo.query(AuditQuery())
    assert len(results) == 1
    assert results[0].event_id == "fresh"


async def test_purge_expired_ignores_events_without_ttl(repo):
    """Events without TTL must never be purged."""
    past = datetime.now(UTC) - timedelta(hours=24)
    event = SleipnirEvent(
        event_id="no-ttl",
        event_type="ravn.tool.complete",
        source="ravn:agent",
        payload={},
        summary="permanent event",
        urgency=0.3,
        domain="code",
        timestamp=past,
        ttl=None,
    )
    await repo.append(event)

    deleted = await repo.purge_expired()
    assert deleted == 0

    results = await repo.query(AuditQuery())
    assert len(results) == 1


async def test_purge_expired_keeps_live_ttl_events(repo):
    """Events whose TTL has not yet elapsed must be kept."""
    future_ttl = SleipnirEvent(
        event_id="live",
        event_type="ravn.tool.complete",
        source="ravn:agent",
        payload={},
        summary="live event",
        urgency=0.3,
        domain="code",
        timestamp=datetime.now(UTC),
        ttl=3600,  # expires in 1 hour
    )
    await repo.append(future_ttl)

    deleted = await repo.purge_expired()
    assert deleted == 0

    results = await repo.query(AuditQuery())
    assert len(results) == 1


async def test_purge_returns_zero_when_empty(repo):
    deleted = await repo.purge_expired()
    assert deleted == 0


async def test_query_naive_timestamp_gets_utc(repo):
    """Rows stored with a naive ISO timestamp should be read back with UTC tzinfo."""
    evt = make_event(event_id="ts-test")
    await repo.append(evt)

    # Tamper: overwrite the stored timestamp to a naive ISO string (no tzinfo)
    conn = repo._conn  # direct access for test
    conn.execute(
        "UPDATE sleipnir_events SET timestamp = ? WHERE event_id = ?",
        ("2024-01-15T12:00:00", "ts-test"),
    )
    conn.commit()

    results = await repo.query(AuditQuery())
    assert results[0].timestamp.tzinfo is not None
