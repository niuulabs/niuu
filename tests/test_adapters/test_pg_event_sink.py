"""Tests for PostgresEventSink adapter."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg

from volundr.adapters.outbound.pg_event_sink import PostgresEventSink
from volundr.domain.models import SessionEvent, SessionEventType


def _make_event(**overrides) -> SessionEvent:
    defaults = {
        "id": uuid4(),
        "session_id": uuid4(),
        "event_type": SessionEventType.MESSAGE_ASSISTANT,
        "timestamp": datetime.now(UTC),
        "data": {"content_preview": "hello", "content_length": 5},
        "sequence": 0,
        "tokens_in": 100,
        "tokens_out": 50,
        "cost": Decimal("0.003"),
        "model": "claude-sonnet-4-20250514",
    }
    defaults.update(overrides)
    return SessionEvent(**defaults)


class TestPostgresEventSinkEmit:
    """Tests for EventSink.emit (write-side)."""

    async def test_emit_inserts_row(self):
        pool = AsyncMock()
        sink = PostgresEventSink(pool, buffer_size=1)
        event = _make_event()

        await sink.emit(event)

        pool.execute.assert_called_once()
        args = pool.execute.call_args[0]
        assert "INSERT INTO session_events" in args[0]
        assert args[1] == event.id
        assert args[2] == event.session_id
        assert args[3] == event.event_type.value

    async def test_emit_batch_uses_executemany(self):
        pool = AsyncMock()
        sink = PostgresEventSink(pool)
        events = [_make_event(sequence=i) for i in range(3)]

        await sink.emit_batch(events)

        pool.executemany.assert_called_once()
        args = pool.executemany.call_args[0]
        assert "INSERT INTO session_events" in args[0]
        assert len(args[1]) == 3

    async def test_emit_batch_empty_is_noop(self):
        pool = AsyncMock()
        sink = PostgresEventSink(pool)

        await sink.emit_batch([])
        pool.executemany.assert_not_called()

    async def test_buffered_emit_accumulates(self):
        pool = AsyncMock()
        sink = PostgresEventSink(pool, buffer_size=3)
        sid = uuid4()

        # First two events should buffer, not insert
        await sink.emit(_make_event(session_id=sid, sequence=0))
        await sink.emit(_make_event(session_id=sid, sequence=1))
        pool.executemany.assert_not_called()
        pool.execute.assert_not_called()

        # Third event triggers flush
        await sink.emit(_make_event(session_id=sid, sequence=2))
        pool.executemany.assert_called_once()
        assert len(pool.executemany.call_args[0][1]) == 3

    async def test_flush_sends_buffered_events(self):
        pool = AsyncMock()
        sink = PostgresEventSink(pool, buffer_size=10)

        await sink.emit(_make_event(sequence=0))
        await sink.emit(_make_event(sequence=1))
        pool.executemany.assert_not_called()

        await sink.flush()
        pool.executemany.assert_called_once()
        assert len(pool.executemany.call_args[0][1]) == 2

    async def test_flush_empty_buffer_is_noop(self):
        pool = AsyncMock()
        sink = PostgresEventSink(pool, buffer_size=10)

        await sink.flush()
        pool.executemany.assert_not_called()

    async def test_close_flushes_buffer(self):
        pool = AsyncMock()
        sink = PostgresEventSink(pool, buffer_size=10)

        await sink.emit(_make_event())
        await sink.close()
        pool.executemany.assert_called_once()

    async def test_sink_name(self):
        sink = PostgresEventSink(AsyncMock())
        assert sink.sink_name == "postgres"

    async def test_healthy_default_true(self):
        sink = PostgresEventSink(AsyncMock())
        assert sink.healthy is True

    async def test_event_to_args_handles_none_cost(self):
        event = _make_event(cost=None, tokens_in=None, tokens_out=None, model=None)
        args = PostgresEventSink._event_to_args(event)
        # cost (index 7) should be None
        assert args[7] is None
        # tokens_in (index 5) should be None
        assert args[5] is None


class TestNulSanitization:
    """PostgreSQL text/JSONB cannot store U+0000 or lone surrogates; event.data
    AND the text columns must be sanitized (U+FFFD) so a poisoned event never
    wedges the whole executemany batch (the real bug)."""

    NUL = chr(0)
    SUR = chr(0xD800)
    R = "�"

    @classmethod
    def _decoded_data(cls, data: dict) -> dict:
        # _event_to_args is the single serialization choke-point for both
        # _insert_one and emit_batch; data is index 4.
        args = PostgresEventSink._event_to_args(_make_event(data=data))
        return json.loads(args[4])

    def test_nul_in_string_value_replaced(self):
        assert self._decoded_data({"content_preview": f"hang{self.NUL}listing"}) == {
            "content_preview": f"hang{self.R}listing"
        }

    def test_nul_nested_in_dict_and_list_replaced(self):
        decoded = self._decoded_data(
            {
                "outer": {"inner": f"a{self.NUL}b"},
                "items": [f"x{self.NUL}y", {"deep": f"p{self.NUL}q"}],
            }
        )
        assert decoded["outer"]["inner"] == f"a{self.R}b"
        assert decoded["items"] == [f"x{self.R}y", {"deep": f"p{self.R}q"}]

    def test_nul_in_dict_key_replaced(self):
        assert self._decoded_data({f"ke{self.NUL}y": "value"}) == {f"ke{self.R}y": "value"}

    def test_lone_surrogate_replaced(self):
        assert self._decoded_data({"text": f"p{self.SUR}q"}) == {"text": f"p{self.R}q"}

    def test_text_columns_event_type_and_model_sanitized(self):
        # event_type ($3) / model ($10) are plain text — scrub them too.
        args = PostgresEventSink._event_to_args(_make_event(model=f"opus{self.NUL}4"))
        assert args[9] == f"opus{self.R}4"  # model

    def test_valid_unicode_preserved(self):
        assert self._decoded_data({"text": "café — 日本語 😀"}) == {"text": "café — 日本語 😀"}

    def test_no_nul_data_unchanged_byte_for_byte(self):
        data = {"content_preview": "hello", "content_length": 5}
        args = PostgresEventSink._event_to_args(_make_event(data=data))
        assert args[4] == json.dumps(data)

    async def test_emit_batch_replaces_nul(self):
        pool = AsyncMock()
        sink = PostgresEventSink(pool)
        events = [_make_event(sequence=i, data={"t": f"v{self.NUL}{i}"}) for i in range(3)]

        await sink.emit_batch(events)

        rows = pool.executemany.call_args[0][1]
        for i, row in enumerate(rows):
            assert json.loads(row[4]) == {"t": f"v{self.R}{i}"}

    async def test_emit_batch_retries_scrubbed_when_insert_still_raises(self):
        # Defensive: a still-thrown UntranslatableCharacterError must not drop the batch.
        pool = AsyncMock()
        pool.executemany = AsyncMock(
            side_effect=[asyncpg.exceptions.UntranslatableCharacterError("boom"), None]
        )
        sink = PostgresEventSink(pool)

        await sink.emit_batch([_make_event(data={"t": "ok"})])

        assert pool.executemany.await_count == 2

    async def test_insert_one_retries_scrubbed_when_insert_still_raises(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(
            side_effect=[asyncpg.exceptions.UntranslatableCharacterError("boom"), None]
        )
        sink = PostgresEventSink(pool, buffer_size=1)

        await sink.emit(_make_event(data={"t": "ok"}))

        assert pool.execute.await_count == 2


class TestPostgresEventSinkRead:
    """Tests for SessionEventRepository (read-side)."""

    async def test_get_events_basic(self):
        pool = AsyncMock()
        session_id = uuid4()
        now = datetime.now(UTC)

        pool.fetch.return_value = [
            {
                "id": uuid4(),
                "session_id": session_id,
                "event_type": "message_assistant",
                "timestamp": now,
                "data": {"content_preview": "hi"},
                "sequence": 0,
                "tokens_in": 10,
                "tokens_out": 20,
                "cost": Decimal("0.001"),
                "duration_ms": 500,
                "model": "claude-sonnet-4-20250514",
            }
        ]

        sink = PostgresEventSink(pool)
        events = await sink.get_events(session_id)

        assert len(events) == 1
        assert events[0].event_type == SessionEventType.MESSAGE_ASSISTANT
        assert events[0].tokens_in == 10
        pool.fetch.assert_called_once()
        query = pool.fetch.call_args[0][0]
        assert "session_id = $1" in query

    async def test_get_events_with_type_filter(self):
        pool = AsyncMock()
        pool.fetch.return_value = []

        sink = PostgresEventSink(pool)
        await sink.get_events(
            uuid4(),
            event_types=[SessionEventType.FILE_MODIFIED],
        )

        query = pool.fetch.call_args[0][0]
        assert "event_type IN" in query

    async def test_get_events_with_time_filters(self):
        pool = AsyncMock()
        pool.fetch.return_value = []

        sink = PostgresEventSink(pool)
        now = datetime.now(UTC)
        await sink.get_events(uuid4(), after=now, before=now)

        query = pool.fetch.call_args[0][0]
        assert "timestamp >" in query
        assert "timestamp <" in query

    async def test_get_event_counts(self):
        pool = AsyncMock()
        pool.fetch.return_value = [
            {"event_type": "message_assistant", "cnt": 10},
            {"event_type": "file_modified", "cnt": 5},
        ]

        sink = PostgresEventSink(pool)
        counts = await sink.get_event_counts(uuid4())

        assert counts == {"message_assistant": 10, "file_modified": 5}

    async def test_get_token_timeline(self):
        pool = AsyncMock()
        pool.fetch.return_value = [
            {"bucket": 1000, "tokens_in": 100, "tokens_out": 50, "cost": Decimal("0.01")}
        ]

        sink = PostgresEventSink(pool)
        timeline = await sink.get_token_timeline(uuid4(), bucket_seconds=300)

        assert len(timeline) == 1
        assert timeline[0]["tokens_in"] == 100

    async def test_delete_by_session(self):
        pool = AsyncMock()
        pool.execute.return_value = "DELETE 5"

        sink = PostgresEventSink(pool)
        count = await sink.delete_by_session(uuid4())

        assert count == 5

    async def test_row_to_event_handles_string_data(self):
        row = MagicMock()
        row.__getitem__ = lambda self, k: {
            "id": uuid4(),
            "session_id": uuid4(),
            "event_type": "terminal_command",
            "timestamp": datetime.now(UTC),
            "data": '{"command": "ls"}',
            "sequence": 1,
            "tokens_in": None,
            "tokens_out": None,
            "cost": None,
            "duration_ms": 100,
            "model": None,
        }[k]

        event = PostgresEventSink._row_to_event(row)
        assert event.event_type == SessionEventType.TERMINAL_COMMAND
        assert event.data == {"command": "ls"}
