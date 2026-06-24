"""Tests for PostgresEventSink adapter."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

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
    """PostgreSQL JSONB cannot store U+0000; event.data must be sanitized so a
    NUL-bearing event never wedges the whole executemany batch (the real bug)."""

    @staticmethod
    def _serialized_data(data: dict) -> str:
        # _event_to_args is the single serialization choke-point for both
        # _insert_one and emit_batch.
        args = PostgresEventSink._event_to_args(_make_event(data=data))
        return args[4]  # data is index 4

    def test_nul_in_string_value_stripped(self):
        nul = chr(0)
        serialized = self._serialized_data({"content_preview": f"hang{nul}listing"})
        assert "\\u0000" not in serialized
        assert "hanglisting" in serialized

    def test_nul_nested_in_dict_and_list_stripped(self):
        nul = chr(0)
        data = {
            "outer": {"inner": f"a{nul}b"},
            "items": [f"x{nul}y", {"deep": f"p{nul}q"}],
        }
        serialized = self._serialized_data(data)
        assert "\\u0000" not in serialized
        assert "ab" in serialized
        assert "xy" in serialized
        assert "pq" in serialized

    def test_nul_in_dict_key_stripped(self):
        nul = chr(0)
        serialized = self._serialized_data({f"ke{nul}y": "value"})
        assert "\\u0000" not in serialized
        assert '"key"' in serialized

    def test_multiple_nuls_stripped(self):
        nul = chr(0)
        serialized = self._serialized_data({"text": f"{nul}a{nul}{nul}b{nul}"})
        assert "\\u0000" not in serialized
        assert '"ab"' in serialized

    def test_valid_unicode_preserved(self):
        import json

        serialized = self._serialized_data({"text": "café — 日本語 😀"})
        assert "\\u0000" not in serialized
        assert json.loads(serialized) == {"text": "café — 日本語 😀"}

    def test_no_nul_data_unchanged_byte_for_byte(self):
        import json

        data = {"content_preview": "hello", "content_length": 5}
        serialized = self._serialized_data(data)
        assert serialized == json.dumps(data)

    async def test_emit_batch_serializes_without_nul_escape(self):
        nul = chr(0)
        pool = AsyncMock()
        sink = PostgresEventSink(pool)
        events = [_make_event(sequence=i, data={"t": f"v{nul}{i}"}) for i in range(3)]

        await sink.emit_batch(events)

        rows = pool.executemany.call_args[0][1]
        for row in rows:
            assert "\\u0000" not in row[4]


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
