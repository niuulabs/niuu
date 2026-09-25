"""Tests for PostgresSpanRepository's w3c_trace_id column."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_spans import PostgresSpanRepository
from volundr.domain.models import SessionSpan, SessionSpanStatus


def _row(**overrides) -> dict:
    row = {
        "id": uuid4(),
        "session_id": uuid4(),
        "trace_id": uuid4(),
        "parent_span_id": None,
        "kind": "turn.local",
        "name": "turn",
        "status": "running",
        "started_at": datetime.now(UTC),
        "ended_at": None,
        "duration_ms": None,
        "actor_type": None,
        "actor_id": None,
        "actor_label": None,
        "source_service": "skuld",
        "attributes": {},
        "w3c_trace_id": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def repository():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    return PostgresSpanRepository(pool), pool


def _span(**overrides) -> SessionSpan:
    defaults = dict(
        id=uuid4(),
        session_id=uuid4(),
        trace_id=uuid4(),
        kind="turn.local",
        name="turn",
        started_at=datetime.now(UTC),
        source_service="skuld",
    )
    defaults.update(overrides)
    return SessionSpan(**defaults)


class TestUpsertSpanW3cTraceId:
    async def test_upsert_passes_w3c_trace_id_as_a_bind_parameter(self, repository):
        store, pool = repository
        pool.fetchrow.return_value = _row(w3c_trace_id="0123456789abcdef0123456789abcdef")
        span = _span(w3c_trace_id="0123456789abcdef0123456789abcdef")

        result = await store.upsert_span(span)

        args = pool.fetchrow.call_args.args
        assert args[-1] == "0123456789abcdef0123456789abcdef"
        assert result.w3c_trace_id == "0123456789abcdef0123456789abcdef"

    async def test_upsert_with_no_active_trace_passes_none(self, repository):
        store, pool = repository
        pool.fetchrow.return_value = _row()
        span = _span()
        assert span.w3c_trace_id is None

        await store.upsert_span(span)

        args = pool.fetchrow.call_args.args
        assert args[-1] is None

    async def test_upsert_sql_coalesces_on_conflict_so_a_retried_start_keeps_it(self, repository):
        """A span POSTed twice (e.g. Skuld retries /spans/start) must not let
        a second call with no ambient trace null out the first call's
        w3c_trace_id — COALESCE(EXCLUDED.w3c_trace_id, session_spans.w3c_trace_id)
        keeps whichever value is non-null."""
        store, pool = repository
        pool.fetchrow.return_value = _row()

        await store.upsert_span(_span())

        sql = pool.fetchrow.call_args.args[0]
        assert "w3c_trace_id = COALESCE(EXCLUDED.w3c_trace_id, session_spans.w3c_trace_id)" in sql

    def test_row_to_span_reads_w3c_trace_id_back(self, repository):
        store, _ = repository
        row = _row(w3c_trace_id="abcdefabcdefabcdefabcdefabcdefab")

        span = store._row_to_span(row)

        assert span.w3c_trace_id == "abcdefabcdefabcdefabcdefabcdefab"
        assert span.status == SessionSpanStatus.RUNNING
