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


class TestNulSanitization:
    """PostgreSQL JSONB cannot store U+0000; the payload must be sanitized so a
    NUL-bearing frame never wedges the whole executemany batch (the real bug)."""

    @staticmethod
    async def _serialized_payload(payload: dict) -> str:
        pool = AsyncMock()
        log = PostgresSessionEventLog(pool)
        await log.append([_make_entry(payload=payload)])
        # args is list[tuple]; payload is index 5 of the first row
        args = pool.executemany.call_args[0][1]
        return args[0][5]

    async def test_nul_in_string_value_stripped(self):
        nul = chr(0)
        serialized = await self._serialized_payload({"text": f"crash{nul}dump"})
        assert "\\u0000" not in serialized
        assert "crashdump" in serialized

    async def test_nul_nested_in_dict_and_list_stripped(self):
        nul = chr(0)
        payload = {
            "outer": {"inner": f"a{nul}b"},
            "items": [f"x{nul}y", {"deep": f"p{nul}q"}],
        }
        serialized = await self._serialized_payload(payload)
        assert "\\u0000" not in serialized
        assert "ab" in serialized
        assert "xy" in serialized
        assert "pq" in serialized

    async def test_nul_in_dict_key_stripped(self):
        nul = chr(0)
        serialized = await self._serialized_payload({f"ke{nul}y": "value"})
        assert "\\u0000" not in serialized
        assert '"key"' in serialized

    async def test_multiple_nuls_stripped(self):
        nul = chr(0)
        serialized = await self._serialized_payload({"text": f"{nul}a{nul}{nul}b{nul}"})
        assert "\\u0000" not in serialized
        assert '"ab"' in serialized

    async def test_valid_unicode_preserved(self):
        # ensure_ascii defaults True -> non-ASCII becomes \uXXXX escapes, not stripped
        serialized = await self._serialized_payload({"text": "café — 日本語 😀"})
        assert "\\u0000" not in serialized
        import json

        assert json.loads(serialized) == {"text": "café — 日本語 😀"}

    async def test_no_nul_payload_unchanged_byte_for_byte(self):
        import json

        payload = {"type": "assistant", "content": [{"type": "text", "text": "hi"}]}
        serialized = await self._serialized_payload(payload)
        assert serialized == json.dumps(payload)


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
