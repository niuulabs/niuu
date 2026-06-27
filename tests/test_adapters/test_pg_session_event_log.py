"""Tests for the PostgresSessionEventLog adapter."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg

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
    """PostgreSQL text/JSONB cannot store U+0000 or lone surrogates; the payload
    AND the text columns must be sanitized (U+FFFD) so a poisoned frame never
    wedges the whole executemany batch (the real bug)."""

    NUL = chr(0)
    SUR = chr(0xD800)
    R = "�"

    @staticmethod
    async def _row_args(**entry_overrides) -> tuple:
        pool = AsyncMock()
        log = PostgresSessionEventLog(pool)
        await log.append([_make_entry(**entry_overrides)])
        return pool.executemany.call_args[0][1][0]  # first row's arg tuple

    async def _payload(self, payload: dict) -> dict:
        args = await self._row_args(payload=payload)
        return json.loads(args[5])

    async def test_nul_in_string_value_replaced(self):
        decoded = await self._payload({"text": f"crash{self.NUL}dump"})
        assert decoded == {"text": f"crash{self.R}dump"}

    async def test_nul_nested_in_dict_and_list_replaced(self):
        decoded = await self._payload(
            {
                "outer": {"inner": f"a{self.NUL}b"},
                "items": [f"x{self.NUL}y", {"deep": f"p{self.NUL}q"}],
            }
        )
        assert decoded["outer"]["inner"] == f"a{self.R}b"
        assert decoded["items"] == [f"x{self.R}y", {"deep": f"p{self.R}q"}]

    async def test_nul_in_dict_key_replaced(self):
        decoded = await self._payload({f"ke{self.NUL}y": "value"})
        assert decoded == {f"ke{self.R}y": "value"}

    async def test_lone_surrogate_replaced(self):
        decoded = await self._payload({"text": f"p{self.SUR}q"})
        assert decoded == {"text": f"p{self.R}q"}

    async def test_text_columns_kind_role_request_id_sanitized(self):
        # The plain text columns ($3/$4/$5) also 500 on a NUL — scrub them too.
        args = await self._row_args(
            kind=f"assi{self.NUL}stant",
            role=f"u{self.SUR}ser",
            request_id=f"req{self.NUL}id",
        )
        assert args[2] == f"assi{self.R}stant"  # kind
        assert args[3] == f"u{self.R}ser"  # role
        assert args[4] == f"req{self.R}id"  # request_id

    async def test_valid_unicode_preserved(self):
        decoded = await self._payload({"text": "café — 日本語 😀"})
        assert decoded == {"text": "café — 日本語 😀"}

    async def test_no_nul_payload_unchanged_byte_for_byte(self):
        payload = {"type": "assistant", "content": [{"type": "text", "text": "hi"}]}
        args = await self._row_args(payload=payload)
        assert args[5] == json.dumps(payload)

    async def test_append_retries_scrubbed_when_insert_still_raises(self):
        # Defensive layer: a still-thrown UntranslatableCharacterError must NOT
        # 500 + drop the batch — append retries with a force-scrubbed payload.
        pool = AsyncMock()
        pool.executemany = AsyncMock(
            side_effect=[asyncpg.exceptions.UntranslatableCharacterError("boom"), None]
        )
        log = PostgresSessionEventLog(pool)

        submitted = await log.append([_make_entry(payload={"text": "ok"})])

        assert submitted == 1
        assert pool.executemany.await_count == 2  # initial + scrubbed retry


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
