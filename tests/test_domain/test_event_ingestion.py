"""Tests for EventIngestionService."""

from datetime import UTC, datetime
from uuid import uuid4

from volundr.domain.models import SessionEvent, SessionEventType
from volundr.domain.ports import EventSink
from volundr.domain.services.event_ingestion import EventIngestionService


class FakeEventSink(EventSink):
    """In-memory event sink for testing."""

    def __init__(self, *, fail: bool = False, name: str = "fake"):
        self._events: list[SessionEvent] = []
        self._batches: list[list[SessionEvent]] = []
        self._fail = fail
        self._name = name
        self._flushed = False
        self._closed = False

    async def emit(self, event: SessionEvent) -> None:
        if self._fail:
            raise RuntimeError(f"Sink {self._name} failed")
        self._events.append(event)

    async def emit_batch(self, events: list[SessionEvent]) -> None:
        if self._fail:
            raise RuntimeError(f"Sink {self._name} batch failed")
        self._batches.append(events)
        self._events.extend(events)

    async def flush(self) -> None:
        self._flushed = True

    async def close(self) -> None:
        self._closed = True

    @property
    def sink_name(self) -> str:
        return self._name

    @property
    def healthy(self) -> bool:
        return not self._fail


def _make_event(**overrides) -> SessionEvent:
    defaults = {
        "id": uuid4(),
        "session_id": uuid4(),
        "event_type": SessionEventType.MESSAGE_ASSISTANT,
        "timestamp": datetime.now(UTC),
        "data": {"content_preview": "hello"},
        "sequence": 0,
    }
    defaults.update(overrides)
    return SessionEvent(**defaults)


class TestEventIngestionService:
    """Tests for EventIngestionService fan-out."""

    async def test_ingest_fans_out_to_all_sinks(self):
        sink_a = FakeEventSink(name="a")
        sink_b = FakeEventSink(name="b")
        service = EventIngestionService(sinks=[sink_a, sink_b])

        event = _make_event()
        await service.ingest(event)

        assert len(sink_a._events) == 1
        assert len(sink_b._events) == 1
        assert sink_a._events[0].id == event.id
        assert sink_b._events[0].id == event.id

    async def test_ingest_batch_fans_out_to_all_sinks(self):
        sink_a = FakeEventSink(name="a")
        sink_b = FakeEventSink(name="b")
        service = EventIngestionService(sinks=[sink_a, sink_b])

        events = [_make_event(sequence=i) for i in range(3)]
        await service.ingest_batch(events)

        assert len(sink_a._batches) == 1
        assert len(sink_a._batches[0]) == 3
        assert len(sink_b._batches) == 1

    async def test_ingest_batch_empty_is_noop(self):
        sink = FakeEventSink()
        service = EventIngestionService(sinks=[sink])
        await service.ingest_batch([])
        assert len(sink._batches) == 0

    async def test_sink_failure_isolated(self):
        """A failing sink should not block other sinks."""
        good_sink = FakeEventSink(name="good")
        bad_sink = FakeEventSink(fail=True, name="bad")
        service = EventIngestionService(sinks=[good_sink, bad_sink])

        event = _make_event()
        await service.ingest(event)

        assert len(good_sink._events) == 1
        assert len(bad_sink._events) == 0

    async def test_sink_batch_failure_isolated(self):
        good_sink = FakeEventSink(name="good")
        bad_sink = FakeEventSink(fail=True, name="bad")
        service = EventIngestionService(sinks=[good_sink, bad_sink])

        events = [_make_event(sequence=i) for i in range(2)]
        await service.ingest_batch(events)

        assert len(good_sink._events) == 2
        assert len(bad_sink._events) == 0

    async def test_flush_all_flushes_every_sink(self):
        sink_a = FakeEventSink(name="a")
        sink_b = FakeEventSink(name="b")
        service = EventIngestionService(sinks=[sink_a, sink_b])

        await service.flush_all()

        assert sink_a._flushed
        assert sink_b._flushed

    async def test_close_all_closes_every_sink(self):
        sink_a = FakeEventSink(name="a")
        sink_b = FakeEventSink(name="b")
        service = EventIngestionService(sinks=[sink_a, sink_b])

        await service.close_all()

        assert sink_a._flushed
        assert sink_a._closed
        assert sink_b._flushed
        assert sink_b._closed

    async def test_sink_health_reports_status(self):
        good = FakeEventSink(name="good")
        bad = FakeEventSink(fail=True, name="bad")
        service = EventIngestionService(sinks=[good, bad])

        health = service.sink_health()
        assert health == {"good": True, "bad": False}

    async def test_no_sinks_ingest_succeeds(self):
        service = EventIngestionService(sinks=[])
        event = _make_event()
        await service.ingest(event)

    async def test_ingest_multiple_events_sequentially(self):
        sink = FakeEventSink()
        service = EventIngestionService(sinks=[sink])

        for i in range(5):
            await service.ingest(_make_event(sequence=i))

        assert len(sink._events) == 5

    async def test_flush_all_continues_on_failure(self):
        """A failing flush should not prevent other sinks from flushing."""

        class FailingFlushSink(FakeEventSink):
            async def flush(self) -> None:
                raise RuntimeError("flush exploded")

        failing = FailingFlushSink(name="failing")
        good = FakeEventSink(name="good")
        service = EventIngestionService(sinks=[failing, good])

        await service.flush_all()

        assert good._flushed

    async def test_close_all_continues_on_failure(self):
        """A failing close should not prevent other sinks from closing."""

        class FailingCloseSink(FakeEventSink):
            async def close(self) -> None:
                raise RuntimeError("close exploded")

        failing = FailingCloseSink(name="failing")
        good = FakeEventSink(name="good")
        service = EventIngestionService(sinks=[failing, good])

        await service.close_all()

        assert good._flushed
        assert good._closed
