"""Tests for the Ting EventBus and SSE endpoint."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.api.events import _sse_generator, create_events_router, resolve_event_bus
from ting.ports.event_bus import EventBusPort, TingEvent

# ---------------------------------------------------------------------------
# TingEvent
# ---------------------------------------------------------------------------


class TestTingEvent:
    def test_to_sse_format(self):
        event = TingEvent(
            id="fixed-id",
            event="dispatcher.state",
            data={"running": True, "threshold": 0.8, "queue_depth": 2},
        )
        sse = event.to_sse()
        assert sse == (
            "id: fixed-id\nevent: dispatcher.state\n"
            'data: {"running": true, "threshold": 0.8, "queue_depth": 2}\n\n'
        )

    def test_id_auto_generated(self):
        e1 = TingEvent(event="session.spawned", data={})
        e2 = TingEvent(event="session.spawned", data={})
        assert e1.id
        assert e1.id != e2.id

    def test_timestamp_auto_generated(self):
        from datetime import UTC, datetime

        e = TingEvent(event="session.spawned", data={})
        assert isinstance(e.timestamp, datetime)
        assert e.timestamp.tzinfo is UTC

    def test_to_sse_ends_with_double_newline(self):
        event = TingEvent(
            id="x",
            event="phase.unlocked",
            data={"saga_id": "s1", "phase_id": "p1", "phase_name": "P1"},
        )
        assert event.to_sse().endswith("\n\n")


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


class TestEventBus:
    def test_initial_state(self):
        bus = InMemoryEventBus(max_clients=5)
        assert bus.client_count == 0
        assert not bus.at_capacity
        assert bus.get_snapshot() == []

    def test_subscribe_increments_count(self):
        bus = InMemoryEventBus(max_clients=5)
        bus.subscribe()
        assert bus.client_count == 1

    def test_unsubscribe_decrements_count(self):
        bus = InMemoryEventBus(max_clients=5)
        q = bus.subscribe()
        bus.unsubscribe(q)
        assert bus.client_count == 0

    def test_unsubscribe_nonexistent_is_noop(self):
        bus = InMemoryEventBus(max_clients=5)
        orphan: asyncio.Queue = asyncio.Queue()
        bus.unsubscribe(orphan)  # must not raise

    def test_at_capacity_when_full(self):
        bus = InMemoryEventBus(max_clients=2)
        bus.subscribe()
        assert not bus.at_capacity
        bus.subscribe()
        assert bus.at_capacity

    async def test_emit_puts_event_in_all_queues(self):
        bus = InMemoryEventBus(max_clients=5)
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        event = TingEvent(
            event="session.spawned",
            data={"session_id": "s1", "run_id": "r1", "branch": "feat/x"},
        )
        await bus.emit(event)
        assert q1.qsize() == 1
        assert q2.qsize() == 1
        received = await q1.get()
        assert received is event

    async def test_emit_skips_unsubscribed_queues(self):
        bus = InMemoryEventBus(max_clients=5)
        q = bus.subscribe()
        bus.unsubscribe(q)
        event = TingEvent(event="session.spawned", data={})
        await bus.emit(event)
        assert q.qsize() == 0

    async def test_emit_saves_snapshot_for_state_events(self):
        bus = InMemoryEventBus(max_clients=5)
        event = TingEvent(
            event="dispatcher.state",
            data={"running": False, "threshold": 0.8, "queue_depth": 0},
        )
        await bus.emit(event)
        snapshot = bus.get_snapshot()
        assert len(snapshot) == 1
        assert snapshot[0] is event

    async def test_emit_does_not_snapshot_transient_events(self):
        bus = InMemoryEventBus(max_clients=5)
        for ev_type in (
            "session.spawned",
            "session.stopped",
            "session.failed",
            "run.state_changed",
            "confidence.updated",
            "dispatcher.log",
            "saga.pr_created",
            "phase.unlocked",
        ):
            await bus.emit(TingEvent(event=ev_type, data={}))
        assert bus.get_snapshot() == []

    async def test_snapshot_updated_on_repeated_emit(self):
        bus = InMemoryEventBus(max_clients=5)
        e1 = TingEvent(
            event="dispatcher.state",
            data={"running": False, "threshold": 0.8, "queue_depth": 0},
        )
        e2 = TingEvent(
            event="dispatcher.state",
            data={"running": True, "threshold": 0.8, "queue_depth": 3},
        )
        await bus.emit(e1)
        await bus.emit(e2)
        snapshot = bus.get_snapshot()
        assert len(snapshot) == 1
        assert snapshot[0].data["running"] is True

    async def test_get_snapshot_returns_copy(self):
        bus = InMemoryEventBus(max_clients=5)
        await bus.emit(
            TingEvent(
                event="dispatcher.state",
                data={"running": False, "threshold": 0.8, "queue_depth": 0},
            )
        )
        s1 = bus.get_snapshot()
        s2 = bus.get_snapshot()
        assert s1 is not s2

    async def test_get_log_returns_emitted_events(self):
        bus = InMemoryEventBus(max_clients=5, log_size=10)
        e1 = TingEvent(event="session.spawned", data={"session_id": "s1"})
        e2 = TingEvent(event="run.state_changed", data={"run_id": "r1"})
        await bus.emit(e1)
        await bus.emit(e2)
        log = bus.get_log(10)
        assert len(log) == 2
        assert log[0] is e1
        assert log[1] is e2

    async def test_get_log_limits_to_n(self):
        bus = InMemoryEventBus(max_clients=5, log_size=20)
        for i in range(10):
            await bus.emit(TingEvent(event="session.spawned", data={"i": i}))
        log = bus.get_log(3)
        assert len(log) == 3
        # Newest 3 — indices 7, 8, 9
        assert log[-1].data["i"] == 9
        assert log[0].data["i"] == 7

    async def test_get_log_ring_buffer_evicts_oldest(self):
        bus = InMemoryEventBus(max_clients=5, log_size=3)
        for i in range(5):
            await bus.emit(TingEvent(event="session.spawned", data={"i": i}))
        log = bus.get_log(10)
        assert len(log) == 3
        assert log[0].data["i"] == 2
        assert log[-1].data["i"] == 4

    async def test_get_log_empty_when_no_events(self):
        bus = InMemoryEventBus(max_clients=5, log_size=10)
        assert bus.get_log(10) == []

    async def test_get_log_returns_list_copy(self):
        bus = InMemoryEventBus(max_clients=5, log_size=10)
        await bus.emit(TingEvent(event="session.spawned", data={}))
        l1 = bus.get_log(10)
        l2 = bus.get_log(10)
        assert l1 is not l2

    async def test_emit_records_all_event_types_in_log(self):
        bus = InMemoryEventBus(max_clients=5, log_size=20)
        event_types = [
            "dispatcher.state",
            "session.spawned",
            "run.state_changed",
            "confidence.updated",
            "dispatcher.log",
        ]
        for ev_type in event_types:
            await bus.emit(TingEvent(event=ev_type, data={}))
        log = bus.get_log(20)
        assert len(log) == len(event_types)
        assert [e.event for e in log] == event_types


# ---------------------------------------------------------------------------
# SSE generator — direct async tests (avoids httpx ASGI transport limitations)
# ---------------------------------------------------------------------------


class TestSseGenerator:
    """Tests for _sse_generator directly — no HTTP transport required."""

    async def test_yields_snapshot_on_start(self):
        bus = InMemoryEventBus(max_clients=5)
        snap = TingEvent(
            id="snap-1",
            event="dispatcher.state",
            data={"running": False, "threshold": 0.8, "queue_depth": 0},
        )
        await bus.emit(snap)

        q = bus.subscribe()
        chunks: list[str] = []
        async for chunk in _sse_generator(bus, q, keepalive_interval=30.0):
            chunks.append(chunk)
            break  # Stop after snapshot

        combined = "".join(chunks)
        assert "event: dispatcher.state" in combined
        assert "id: snap-1" in combined

    async def test_yields_queued_events(self):
        bus = InMemoryEventBus(max_clients=5)
        q = bus.subscribe()
        event = TingEvent(
            id="live-1",
            event="run.state_changed",
            data={
                "run_id": "r1",
                "saga_id": "s1",
                "phase_id": "p1",
                "state": "RUNNING",
                "confidence": 0.9,
            },
        )
        await bus.emit(event)  # pre-queue before generator starts

        chunks: list[str] = []
        async for chunk in _sse_generator(bus, q, keepalive_interval=30.0):
            chunks.append(chunk)
            if "run.state_changed" in chunk:
                break

        combined = "".join(chunks)
        assert "event: run.state_changed" in combined
        assert "id: live-1" in combined

    async def test_yields_keepalive_when_idle(self):
        bus = InMemoryEventBus(max_clients=5)
        q = bus.subscribe()

        chunks: list[str] = []
        async for chunk in _sse_generator(bus, q, keepalive_interval=0.05):
            chunks.append(chunk)
            if ": keepalive" in chunk:
                break

        assert ": keepalive" in "".join(chunks)

    async def test_unsubscribes_on_close(self):
        bus = InMemoryEventBus(max_clients=5)
        snap = TingEvent(
            id="s1",
            event="dispatcher.state",
            data={"running": False, "threshold": 0.8, "queue_depth": 0},
        )
        await bus.emit(snap)  # Populate snapshot so generator yields immediately

        q = bus.subscribe()
        assert bus.client_count == 1

        gen = _sse_generator(bus, q, keepalive_interval=30.0)
        async for _ in gen:
            break  # Snapshot yields immediately; break schedules aclose()
        await gen.aclose()  # Explicitly await cleanup to ensure finally runs

        assert bus.client_count == 0

    async def test_multiple_event_types_delivered(self):
        """All event types flow through the queue correctly."""
        bus = InMemoryEventBus(max_clients=5)
        q = bus.subscribe()

        event_types = [
            ("session.spawned", {"session_id": "s1", "run_id": "r1", "branch": "b"}),
            (
                "session.stopped",
                {"session_id": "s1", "run_id": "r1", "confidence": 0.9, "state": "REVIEW"},
            ),
            ("session.failed", {"session_id": "s1", "run_id": "r1", "retry_count": 1}),
            ("saga.pr_created", {"saga_id": "sg1", "pr_url": "https://example.com/pr/1"}),
            ("phase.unlocked", {"saga_id": "sg1", "phase_id": "p1", "phase_name": "Phase 1"}),
        ]

        for ev_type, data in event_types:
            await bus.emit(TingEvent(event=ev_type, data=data))

        received: list[str] = []
        gen = _sse_generator(bus, q, keepalive_interval=30.0)
        for _ in range(len(event_types)):
            chunk = await gen.__anext__()
            received.append(chunk)

        await gen.aclose()

        combined = "".join(received)
        for ev_type, _ in event_types:
            assert ev_type in combined


# ---------------------------------------------------------------------------
# SSE HTTP endpoint — sync tests (no streaming body needed)
# ---------------------------------------------------------------------------


def _make_app(event_bus: EventBusPort, keepalive_interval: float = 30.0) -> FastAPI:
    app = FastAPI()
    app.include_router(create_events_router(keepalive_interval=keepalive_interval))
    app.dependency_overrides[resolve_event_bus] = lambda: event_bus
    return app


class TestSseEndpointHttp:
    def test_returns_503_when_at_capacity(self):
        bus = InMemoryEventBus(max_clients=1)
        bus.subscribe()  # fill up
        client = TestClient(_make_app(bus))
        resp = client.get("/api/v1/ting/events")
        assert resp.status_code == 503
        assert "limit" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestTingEventOwnerID:
    def test_owner_id_default_empty(self):
        event = TingEvent(event="test.event", data={})
        assert event.owner_id == ""

    def test_owner_id_set(self):
        event = TingEvent(event="test.event", data={}, owner_id="user-1")
        assert event.owner_id == "user-1"

    def test_owner_id_not_in_sse(self):
        """owner_id is metadata for routing, not included in SSE wire format."""
        event = TingEvent(id="x", event="test.event", data={}, owner_id="user-1")
        sse = event.to_sse()
        assert "owner_id" not in sse


class TestEventBusPortIsABC:
    def test_cannot_instantiate_port(self):
        import pytest

        from ting.ports.event_bus import EventBusPort

        with pytest.raises(TypeError):
            EventBusPort()  # type: ignore[abstract]

    def test_in_memory_implements_port(self):
        from ting.ports.event_bus import EventBusPort

        bus = InMemoryEventBus()
        assert isinstance(bus, EventBusPort)


class TestEventsConfig:
    def test_default_values(self):
        from ting.config import EventsConfig

        cfg = EventsConfig()
        assert cfg.max_sse_clients == 10
        assert cfg.keepalive_interval == 15.0
        assert cfg.activity_log_size == 100

    def test_settings_includes_events(self):
        from ting.config import Settings

        s = Settings()
        assert s.events.max_sse_clients == 10
        assert s.events.keepalive_interval == 15.0
        assert s.events.activity_log_size == 100


class TestEventBusConfig:
    def test_default_values(self):
        from ting.config import EventBusConfig

        cfg = EventBusConfig()
        assert cfg.adapter == "ting.adapters.memory_event_bus.InMemoryEventBus"
        assert cfg.kwargs == {}

    def test_settings_includes_event_bus(self):
        from ting.config import Settings

        s = Settings()
        assert s.event_bus.adapter == "ting.adapters.memory_event_bus.InMemoryEventBus"
