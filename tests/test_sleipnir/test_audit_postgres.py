"""Tests for PostgresAuditRepository helper functions.

Integration tests (requiring a live DB) are marked ``integration`` and skipped
by default.  Unit tests here exercise the query builder and row conversion
without touching a real database.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from sleipnir.adapters.audit_postgres import (
    PostgresAuditRepository,
    _build_query,
    _is_simple_prefix_pattern,
    _row_to_event,
    apply_event_type_filter,
)
from sleipnir.ports.audit import AuditQuery
from tests.test_sleipnir.conftest import DEFAULT_TIMESTAMP, make_event

# ---------------------------------------------------------------------------
# _is_simple_prefix_pattern
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("ravn.*", True),
        ("ting.task.*", True),
        ("*", False),  # no prefix — not a simple prefix pattern
        ("ravn.tool.complete", False),  # no wildcard
        ("ravn.*.complete", False),  # wildcard in middle
        ("ravn.?ool.*", False),  # ? wildcard
        ("ravn.[ab]*", False),  # character class
    ],
)
def test_is_simple_prefix_pattern(pattern, expected):
    assert _is_simple_prefix_pattern(pattern) == expected


# ---------------------------------------------------------------------------
# _build_query
# ---------------------------------------------------------------------------


def test_build_query_no_filters():
    sql, params = _build_query(AuditQuery())
    assert "WHERE" not in sql
    assert params[-1] == 100  # default limit


def test_build_query_event_type_like():
    sql, params = _build_query(AuditQuery(event_type_pattern="ravn.*"))
    assert "LIKE" in sql
    assert "ravn.%" in params


def test_build_query_wildcard_no_condition():
    sql, params = _build_query(AuditQuery(event_type_pattern="*"))
    assert "LIKE" not in sql


def test_build_query_from_ts():
    ts = DEFAULT_TIMESTAMP
    sql, params = _build_query(AuditQuery(from_ts=ts))
    assert "timestamp >=" in sql
    assert ts in params


def test_build_query_to_ts():
    ts = DEFAULT_TIMESTAMP
    sql, params = _build_query(AuditQuery(to_ts=ts))
    assert "timestamp <=" in sql
    assert ts in params


def test_build_query_correlation_id():
    sql, params = _build_query(AuditQuery(correlation_id="corr-123"))
    assert "correlation_id =" in sql
    assert "corr-123" in params


def test_build_query_source():
    sql, params = _build_query(AuditQuery(source="ravn:agent"))
    assert "source =" in sql
    assert "ravn:agent" in params


def test_build_query_limit():
    sql, params = _build_query(AuditQuery(limit=42))
    assert params[-1] == 42


def test_build_query_complex_pattern_overfetches():
    """Complex glob patterns cause over-fetch (limit × 10)."""
    q = AuditQuery(event_type_pattern="ravn.*.complete", limit=50)
    _, params = _build_query(q)
    assert params[-1] == 500  # 50 × 10


def test_build_query_all_filters():
    ts_from = DEFAULT_TIMESTAMP
    ts_to = DEFAULT_TIMESTAMP + timedelta(hours=1)
    q = AuditQuery(
        event_type_pattern="ting.*",
        from_ts=ts_from,
        to_ts=ts_to,
        correlation_id="corr",
        source="ting:disp",
        limit=25,
    )
    sql, params = _build_query(q)
    assert "LIKE" in sql
    assert "timestamp >=" in sql
    assert "timestamp <=" in sql
    assert "correlation_id" in sql
    assert "source" in sql


# ---------------------------------------------------------------------------
# apply_event_type_filter
# ---------------------------------------------------------------------------


def test_apply_filter_none_returns_all():
    events = [make_event(event_id=f"e{i}") for i in range(3)]
    assert apply_event_type_filter(events, None) == events


def test_apply_filter_wildcard_returns_all():
    events = [make_event(event_id=f"e{i}") for i in range(3)]
    assert apply_event_type_filter(events, "*") == events


def test_apply_filter_pattern():
    events = [
        make_event(event_id="a", event_type="ravn.tool.complete"),
        make_event(event_id="b", event_type="ravn.step.start"),
        make_event(event_id="c", event_type="ting.task.started"),
    ]
    result = apply_event_type_filter(events, "ravn.*")
    ids = {e.event_id for e in result}
    assert ids == {"a", "b"}


def test_apply_filter_no_match():
    events = [make_event(event_id="a", event_type="ravn.tool.complete")]
    result = apply_event_type_filter(events, "ting.*")
    assert result == []


# ---------------------------------------------------------------------------
# _row_to_event
# ---------------------------------------------------------------------------


def _make_row(**overrides) -> dict:
    base = {
        "event_id": "evt-001",
        "event_type": "ravn.tool.complete",
        "source": "ravn:agent",
        "summary": "done",
        "urgency": 0.5,
        "domain": "code",
        "correlation_id": None,
        "causation_id": None,
        "tenant_id": None,
        "payload": '{"k": "v"}',
        "timestamp": DEFAULT_TIMESTAMP,
        "ttl": None,
    }
    base.update(overrides)
    # Simulate asyncpg.Record with dict-like access
    return MagicMock(**{"__getitem__": lambda self, key: base[key]})


def test_row_to_event_basic():
    row = _make_row()
    event = _row_to_event(row)
    assert event.event_id == "evt-001"
    assert event.event_type == "ravn.tool.complete"
    assert event.payload == {"k": "v"}
    assert event.timestamp.tzinfo is not None


def test_row_to_event_naive_timestamp_gets_utc():
    naive_ts = DEFAULT_TIMESTAMP.replace(tzinfo=None)
    row = _make_row(timestamp=naive_ts)
    event = _row_to_event(row)
    assert event.timestamp.tzinfo == UTC


def test_row_to_event_none_payload():
    row = _make_row(payload=None)
    event = _row_to_event(row)
    assert event.payload == {}


def test_row_to_event_none_urgency():
    row = _make_row(urgency=None)
    event = _row_to_event(row)
    assert event.urgency == 0.0


def test_row_to_event_none_summary():
    row = _make_row(summary=None)
    event = _row_to_event(row)
    assert event.summary == ""


# ---------------------------------------------------------------------------
# PostgresAuditRepository.append (mocked pool)
# ---------------------------------------------------------------------------


async def test_postgres_append_calls_pool_execute():
    pool = AsyncMock()
    pool.execute = AsyncMock()
    repo = PostgresAuditRepository(pool)

    evt = make_event()
    await repo.append(evt)

    pool.execute.assert_called_once()
    call_args = pool.execute.call_args[0]
    assert "INSERT INTO sleipnir_events" in call_args[0]
    assert evt.event_id in call_args


async def test_postgres_append_handles_on_conflict():
    """The INSERT uses ON CONFLICT DO NOTHING — no exception on duplicate."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    repo = PostgresAuditRepository(pool)

    evt = make_event()
    await repo.append(evt)
    await repo.append(evt)  # should not raise

    assert pool.execute.call_count == 2


# ---------------------------------------------------------------------------
# PostgresAuditRepository.purge_expired (mocked pool)
# ---------------------------------------------------------------------------


async def test_postgres_purge_parses_delete_count():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="DELETE 7")
    repo = PostgresAuditRepository(pool)

    count = await repo.purge_expired()
    assert count == 7


async def test_postgres_purge_handles_malformed_result():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="")
    repo = PostgresAuditRepository(pool)

    count = await repo.purge_expired()
    assert count == 0


async def test_postgres_purge_handles_delete_zero():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="DELETE 0")
    repo = PostgresAuditRepository(pool)

    count = await repo.purge_expired()
    assert count == 0


# ---------------------------------------------------------------------------
# PostgresAuditRepository.query (mocked pool)
# ---------------------------------------------------------------------------


async def test_postgres_query_returns_mapped_events():
    row = _make_row()
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[row])
    repo = PostgresAuditRepository(pool)

    results = await repo.query(AuditQuery())
    assert len(results) == 1
    pool.fetch.assert_called_once()


async def test_postgres_query_empty_result():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    repo = PostgresAuditRepository(pool)

    results = await repo.query(AuditQuery())
    assert results == []


def test_row_to_event_dict_payload():
    """asyncpg may decode JSONB as a dict directly (not a JSON string)."""
    row = _make_row(payload={"already": "decoded"})
    event = _row_to_event(row)
    assert event.payload == {"already": "decoded"}


# ---------------------------------------------------------------------------
# PostgresAuditRepository.query — complex pattern filtering
# ---------------------------------------------------------------------------


async def test_postgres_query_complex_pattern_filters_results():
    """Complex glob patterns (e.g. ravn.*.complete) must be applied in Python
    after the over-fetched DB results are returned, and results truncated to limit."""
    rows = [
        _make_row(event_id="a", event_type="ravn.tool.complete"),
        _make_row(event_id="b", event_type="ravn.step.complete"),
        _make_row(event_id="c", event_type="ravn.tool.started"),  # no match
        _make_row(event_id="d", event_type="ting.task.complete"),  # no match
    ]
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    repo = PostgresAuditRepository(pool)

    results = await repo.query(AuditQuery(event_type_pattern="ravn.*.complete", limit=10))

    ids = {e.event_id for e in results}
    assert ids == {"a", "b"}
    # Verify the DB was called with over-fetched limit (10 × 10 = 100)
    call_args = pool.fetch.call_args
    assert 100 in call_args[0]


async def test_postgres_query_complex_pattern_truncates_to_limit():
    """Results after fnmatch filtering must be capped at q.limit."""
    rows = [_make_row(event_id=f"evt-{i}", event_type="ravn.tool.complete") for i in range(20)]
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    repo = PostgresAuditRepository(pool)

    results = await repo.query(AuditQuery(event_type_pattern="ravn.*.complete", limit=5))
    assert len(results) == 5
