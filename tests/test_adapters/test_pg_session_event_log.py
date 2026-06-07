"""Tests for the PostgresSessionEventLog adapter."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.domain.models import SessionLogEntry


def _make_entry(**overrides) -> SessionLogEntry:
    defaults = {
        "session_id": uuid4(),
        "seq": 1,
        "kind": "assistant",
        "payload": {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
        "ts": datetime.now(UTC),
        "role": "assistant",
        "request_id": "forge-web-1",
    }
    defaults.update(overrides)
    return SessionLogEntry(**defaults)


class TestAppend:
    async def test_append_uses_executemany_with_conflict_clause(self):
        pool = AsyncMock()
        log = PostgresSessionEventLog(pool)
        sid = uuid4()
        entries = [_make_entry(session_id=sid, seq=i) for i in range(3)]

        submitted = await log.append(entries)

        assert submitted == 3
        pool.executemany.assert_called_once()
        sql, args = pool.executemany.call_args[0]
        assert "INSERT INTO session_event_log" in sql
        assert "ON CONFLICT (session_id, seq) DO NOTHING" in sql
        assert len(args) == 3
        # payload is serialized to a JSON string for the JSONB column
        assert isinstance(args[0][5], str)

    async def test_append_empty_is_noop(self):
        pool = AsyncMock()
        log = PostgresSessionEventLog(pool)

        submitted = await log.append([])

        assert submitted == 0
        pool.executemany.assert_not_called()


class TestReadAfter:
    async def test_read_after_queries_by_cursor_ordered(self):
        sid = uuid4()
        ts = datetime.now(UTC)
        pool = AsyncMock()
        pool.fetch.return_value = [
            {
                "session_id": sid,
                "seq": 5,
                "kind": "content_block_delta",
                "role": None,
                "request_id": "r1",
                "payload": {"delta": {"text": "x"}},
                "ts": ts,
            }
        ]
        log = PostgresSessionEventLog(pool)

        entries = await log.read_after(sid, after_seq=4, limit=10)

        sql, *params = pool.fetch.call_args[0]
        assert "seq > $2" in sql
        assert "ORDER BY seq ASC" in sql
        assert params == [sid, 4, 10]
        assert len(entries) == 1
        assert entries[0].seq == 5
        assert entries[0].kind == "content_block_delta"
        assert entries[0].payload == {"delta": {"text": "x"}}

    async def test_read_after_decodes_json_string_payload(self):
        sid = uuid4()
        pool = AsyncMock()
        pool.fetch.return_value = [
            {
                "session_id": sid,
                "seq": 1,
                "kind": "tool_result",
                "role": "user",
                "request_id": None,
                "payload": '{"tool_use_id": "abc", "content": "ok"}',
                "ts": datetime.now(UTC),
            }
        ]
        log = PostgresSessionEventLog(pool)

        entries = await log.read_after(sid)

        assert entries[0].payload == {"tool_use_id": "abc", "content": "ok"}


class TestLatestSeq:
    async def test_latest_seq_returns_max(self):
        pool = AsyncMock()
        pool.fetchval.return_value = 42
        log = PostgresSessionEventLog(pool)

        assert await log.latest_seq(uuid4()) == 42

    async def test_latest_seq_zero_when_empty(self):
        pool = AsyncMock()
        pool.fetchval.return_value = None
        log = PostgresSessionEventLog(pool)

        assert await log.latest_seq(uuid4()) == 0
