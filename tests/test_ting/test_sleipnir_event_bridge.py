"""Tests for ting.adapters.sleipnir_event_bridge.SleipnirEventBridge."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.registry import (
    TING_SAGA_COMPLETE,
    TING_SAGA_CREATED,
    TING_SAGA_FAILED,
    TING_SAGA_STEP,
    TING_TASK_COMPLETE,
    TING_TASK_FAILED,
    TING_TASK_QUEUED,
    TING_TASK_STARTED,
)
from sleipnir.testing import EventCapture
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.adapters.sleipnir_event_bridge import SleipnirEventBridge, TingSleipnirBridge
from ting.ports.event_bus import TingEvent

_TS = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)


def _make_ting_event(event: str, data: dict | None = None, owner_id: str = "") -> TingEvent:
    return TingEvent(
        event=event,
        data=data or {},
        owner_id=owner_id,
        id="evt-001",
        timestamp=_TS,
    )


# ---------------------------------------------------------------------------
# Delegation to inner bus
# ---------------------------------------------------------------------------


class TestSleipnirEventBridgeDelegation:
    async def test_emit_calls_inner_bus(self):
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, AsyncMock())

        q = bridge.subscribe()
        event = _make_ting_event("saga.created", {"saga_id": "s-1", "name": "My Saga"})
        await bridge.emit(event)

        received = q.get_nowait()
        assert received is event

    def test_subscribe_returns_queue_from_inner(self):
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, AsyncMock())
        q = bridge.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_unsubscribe_removes_from_inner(self):
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, AsyncMock())
        q = bridge.subscribe()
        assert inner.client_count == 1
        bridge.unsubscribe(q)
        assert inner.client_count == 0

    def test_client_count_delegates(self):
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, AsyncMock())
        bridge.subscribe()
        assert bridge.client_count == 1

    def test_at_capacity_delegates(self):
        inner = InMemoryEventBus(max_clients=1)
        bridge = SleipnirEventBridge(inner, AsyncMock())
        bridge.subscribe()
        assert bridge.at_capacity is True

    def test_get_snapshot_delegates(self):
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, AsyncMock())
        assert bridge.get_snapshot() == []

    def test_get_log_delegates(self):
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, AsyncMock())
        assert bridge.get_log(10) == []


# ---------------------------------------------------------------------------
# Saga event mirroring
# ---------------------------------------------------------------------------


class TestSleipnirEventBridgeSagaMirroring:
    async def test_saga_created_mirrored(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_SAGA_CREATED]) as capture:
            await bridge.emit(
                _make_ting_event("saga.created", {"saga_id": "s-1", "name": "Test Saga"})
            )
            await sleipnir.flush()

        assert len(capture.events) == 1
        evt = capture.events[0]
        assert evt.event_type == TING_SAGA_CREATED
        assert evt.payload["saga_id"] == "s-1"
        assert "Test Saga" in evt.summary

    async def test_saga_step_mirrored(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_SAGA_STEP]) as capture:
            await bridge.emit(_make_ting_event("saga.step", {"saga_id": "s-1", "step": 2}))
            await sleipnir.flush()

        assert len(capture.events) == 1

    async def test_saga_completed_mirrored(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_SAGA_COMPLETE]) as capture:
            await bridge.emit(_make_ting_event("saga.completed", {"saga_id": "s-1"}))
            await sleipnir.flush()

        assert len(capture.events) == 1

    async def test_saga_failed_mirrored(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_SAGA_FAILED]) as capture:
            await bridge.emit(_make_ting_event("saga.failed", {"saga_id": "s-1"}))
            await sleipnir.flush()

        assert len(capture.events) == 1


# ---------------------------------------------------------------------------
# Run event mirroring
# ---------------------------------------------------------------------------


class TestSleipnirEventBridgeRunMirroring:
    async def test_run_queued_mirrored(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_TASK_QUEUED]) as capture:
            await bridge.emit(
                _make_ting_event(
                    "run.state_changed",
                    {"run_id": "r-1", "new_status": "QUEUED"},
                )
            )
            await sleipnir.flush()

        assert len(capture.events) == 1
        assert capture.events[0].event_type == TING_TASK_QUEUED

    async def test_run_running_mirrored(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_TASK_STARTED]) as capture:
            await bridge.emit(
                _make_ting_event(
                    "run.state_changed",
                    {"run_id": "r-1", "new_status": "RUNNING"},
                )
            )
            await sleipnir.flush()

        assert len(capture.events) == 1

    async def test_run_merged_maps_to_task_complete(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_TASK_COMPLETE]) as capture:
            await bridge.emit(
                _make_ting_event(
                    "run.state_changed",
                    {"run_id": "r-1", "new_status": "MERGED"},
                )
            )
            await sleipnir.flush()

        assert len(capture.events) == 1

    async def test_run_failed_mirrored(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_TASK_FAILED]) as capture:
            await bridge.emit(
                _make_ting_event(
                    "run.state_changed",
                    {"run_id": "r-1", "new_status": "FAILED"},
                )
            )
            await sleipnir.flush()

        assert len(capture.events) == 1

    async def test_run_review_status_not_forwarded(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, ["ting.*"]) as capture:
            await bridge.emit(
                _make_ting_event(
                    "run.state_changed",
                    {"run_id": "r-1", "new_status": "REVIEW"},
                )
            )
            await sleipnir.flush()

        assert len(capture.events) == 0


# ---------------------------------------------------------------------------
# Events not forwarded
# ---------------------------------------------------------------------------


class TestSleipnirEventBridgeNoForward:
    async def test_notification_events_not_forwarded(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, ["ting.*"]) as capture:
            await bridge.emit(_make_ting_event("notification.sent", {"channel": "telegram"}))
            await sleipnir.flush()

        assert len(capture.events) == 0

    async def test_unrecognized_event_type_not_forwarded(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, ["ting.*"]) as capture:
            await bridge.emit(_make_ting_event("some.unknown.event", {}))
            await sleipnir.flush()

        assert len(capture.events) == 0


# ---------------------------------------------------------------------------
# Owner/tenant propagation
# ---------------------------------------------------------------------------


class TestSleipnirEventBridgeTenant:
    async def test_owner_id_propagated_as_tenant_id(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = SleipnirEventBridge(inner, sleipnir)

        async with EventCapture(sleipnir, [TING_SAGA_CREATED]) as capture:
            await bridge.emit(
                _make_ting_event(
                    "saga.created",
                    {"saga_id": "s-1", "name": "My Saga"},
                    owner_id="user-42",
                )
            )
            await sleipnir.flush()

        assert capture.events[0].tenant_id == "user-42"


# ---------------------------------------------------------------------------
# Fault tolerance
# ---------------------------------------------------------------------------


class TestSleipnirEventBridgeFaultTolerance:
    async def test_sleipnir_publish_error_does_not_block_inner_bus(self):
        inner = InMemoryEventBus()
        publisher = AsyncMock()
        publisher.publish.side_effect = RuntimeError("sleipnir down")
        bridge = SleipnirEventBridge(inner, publisher)

        q = bridge.subscribe()
        event = _make_ting_event("saga.created", {"saga_id": "s-1", "name": "X"})
        await bridge.emit(event)  # must not raise

        # Inner bus still received the event
        received = q.get_nowait()
        assert received is event


# ---------------------------------------------------------------------------
# TingSleipnirBridge lifecycle
# ---------------------------------------------------------------------------


class TestTingSleipnirBridge:
    async def test_start_creates_background_task(self):
        inner = InMemoryEventBus()
        bridge = TingSleipnirBridge(event_bus=inner, publisher=AsyncMock())
        assert not bridge.is_running
        await bridge.start()
        assert bridge.is_running
        await bridge.stop()

    async def test_start_is_idempotent(self):
        inner = InMemoryEventBus()
        bridge = TingSleipnirBridge(event_bus=inner, publisher=AsyncMock())
        await bridge.start()
        task_before = bridge._task
        await bridge.start()
        assert bridge._task is task_before
        await bridge.stop()

    async def test_stop_cancels_task(self):
        inner = InMemoryEventBus()
        bridge = TingSleipnirBridge(event_bus=inner, publisher=AsyncMock())
        await bridge.start()
        await bridge.stop()
        assert not bridge.is_running
        assert bridge._task is None

    async def test_stop_is_idempotent(self):
        inner = InMemoryEventBus()
        bridge = TingSleipnirBridge(event_bus=inner, publisher=AsyncMock())
        await bridge.stop()  # must not raise when not started
        assert not bridge.is_running

    async def test_stop_unsubscribes_from_event_bus(self):
        inner = InMemoryEventBus()
        bridge = TingSleipnirBridge(event_bus=inner, publisher=AsyncMock())
        await bridge.start()
        await asyncio.sleep(0)  # let _run() reach await q.get()
        assert inner.client_count == 1
        await bridge.stop()
        assert inner.client_count == 0

    async def test_run_loop_mirrors_events_to_sleipnir(self):
        sleipnir = InProcessBus()
        inner = InMemoryEventBus()
        bridge = TingSleipnirBridge(event_bus=inner, publisher=sleipnir)

        async with EventCapture(sleipnir, [TING_SAGA_CREATED]) as capture:
            await bridge.start()
            await asyncio.sleep(0)
            await inner.emit(
                _make_ting_event("saga.created", {"saga_id": "s-1", "name": "BridgeTest"})
            )
            await asyncio.sleep(0)
            await sleipnir.flush()

        await bridge.stop()
        assert len(capture.events) == 1
        assert capture.events[0].event_type == TING_SAGA_CREATED
