"""Tests for the in-process Sleipnir event bus adapter."""

from __future__ import annotations

import asyncio
import logging

import pytest

from sleipnir.adapters.in_process import DEFAULT_RING_BUFFER_DEPTH, InProcessBus
from sleipnir.domain.events import SleipnirEvent
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# publish → subscribe → handler called
# ---------------------------------------------------------------------------


async def test_subscribe_and_receive_event():
    bus = InProcessBus()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    await bus.subscribe(["ravn.*"], handler)
    await bus.publish(make_event())
    await bus.flush()

    assert len(received) == 1
    assert received[0].event_id == "evt-001"


async def test_handler_not_called_for_non_matching_event():
    bus = InProcessBus()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    await bus.subscribe(["ravn.*"], handler)
    await bus.publish(make_event(event_type="ting.saga.created"))
    await bus.flush()

    assert received == []


async def test_multiple_patterns_any_match_triggers_handler():
    bus = InProcessBus()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    await bus.subscribe(["ting.*", "ravn.*"], handler)
    await bus.publish(make_event(event_type="ravn.tool.complete"))
    await bus.publish(make_event(event_type="ting.saga.created"))
    await bus.publish(make_event(event_type="volundr.pr.opened"))
    await bus.flush()

    assert len(received) == 2
    types = {e.event_type for e in received}
    assert types == {"ravn.tool.complete", "ting.saga.created"}


# ---------------------------------------------------------------------------
# Batch publishing
# ---------------------------------------------------------------------------


async def test_publish_batch_delivers_all_events():
    bus = InProcessBus()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    await bus.subscribe(["*"], handler)
    events = [
        make_event(event_type="ravn.tool.complete", event_id="e1"),
        make_event(event_type="ting.saga.created", event_id="e2"),
        make_event(event_type="volundr.pr.opened", event_id="e3"),
    ]
    await bus.publish_batch(events)
    await bus.flush()

    assert len(received) == 3
    assert [e.event_id for e in received] == ["e1", "e2", "e3"]


async def test_publish_batch_preserves_order():
    bus = InProcessBus()
    order: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        order.append(evt.event_id)

    await bus.subscribe(["*"], handler)
    await bus.publish_batch([make_event(event_id=str(i)) for i in range(10)])
    await bus.flush()

    assert order == [str(i) for i in range(10)]


# ---------------------------------------------------------------------------
# Multiple subscribers
# ---------------------------------------------------------------------------


async def test_multiple_subscribers_each_receive_event():
    bus = InProcessBus()
    bucket_a: list[SleipnirEvent] = []
    bucket_b: list[SleipnirEvent] = []

    async def handler_a(evt: SleipnirEvent) -> None:
        bucket_a.append(evt)

    async def handler_b(evt: SleipnirEvent) -> None:
        bucket_b.append(evt)

    await bus.subscribe(["ravn.*"], handler_a)
    await bus.subscribe(["ravn.*"], handler_b)
    await bus.publish(make_event())
    await bus.flush()

    assert len(bucket_a) == 1
    assert len(bucket_b) == 1


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------


async def test_unsubscribe_stops_delivery():
    bus = InProcessBus()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    sub = await bus.subscribe(["ravn.*"], handler)
    await bus.publish(make_event(event_id="e1"))
    await bus.flush()
    await sub.unsubscribe()
    await bus.publish(make_event(event_id="e2"))
    await bus.flush()

    assert len(received) == 1
    assert received[0].event_id == "e1"


async def test_unsubscribe_is_idempotent():
    bus = InProcessBus()

    async def handler(evt: SleipnirEvent) -> None:
        pass

    sub = await bus.subscribe(["ravn.*"], handler)
    await sub.unsubscribe()
    await sub.unsubscribe()  # must not raise


async def test_unsubscribe_removes_from_bus():
    bus = InProcessBus()

    async def handler(evt: SleipnirEvent) -> None:
        pass

    sub = await bus.subscribe(["*"], handler)
    assert len(bus._subscriptions) == 1
    await sub.unsubscribe()
    assert len(bus._subscriptions) == 0


async def test_unsubscribe_drains_queue():
    bus = InProcessBus(ring_buffer_depth=10)

    async def handler(evt: SleipnirEvent) -> None:
        await asyncio.sleep(10)  # Intentionally slow — events pile up

    sub = await bus.subscribe(["*"], handler)
    for i in range(5):
        await bus.publish(make_event(event_id=str(i)))

    await sub.unsubscribe()
    # Queue should be empty after unsubscribe
    assert sub._queue.empty()


# ---------------------------------------------------------------------------
# Handler exception isolation
# ---------------------------------------------------------------------------


async def test_failing_handler_does_not_block_other_handlers():
    bus = InProcessBus()
    received: list[SleipnirEvent] = []

    async def bad_handler(evt: SleipnirEvent) -> None:
        raise RuntimeError("intentional test error")

    async def good_handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    await bus.subscribe(["*"], bad_handler)
    await bus.subscribe(["*"], good_handler)
    await bus.publish(make_event())
    await bus.flush()

    assert len(received) == 1


async def test_handler_exception_is_logged(caplog):
    bus = InProcessBus()

    async def bad_handler(evt: SleipnirEvent) -> None:
        raise ValueError("test error")

    await bus.subscribe(["*"], bad_handler)
    with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.in_process"):
        await bus.publish(make_event())
        await bus.flush()

    assert any("test error" in r.message or "Handler raised" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Global wildcard
# ---------------------------------------------------------------------------


async def test_global_wildcard_receives_all_namespaces():
    bus = InProcessBus()
    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_type)

    await bus.subscribe(["*"], handler)
    for et in [
        "ravn.tool.complete",
        "ting.saga.created",
        "volundr.pr.opened",
        "bifrost.connection.open",
        "system.health.ping",
    ]:
        await bus.publish(make_event(event_type=et))
    await bus.flush()

    assert len(received) == 5


# ---------------------------------------------------------------------------
# Ring buffer / overflow
# ---------------------------------------------------------------------------


async def test_ring_buffer_drops_oldest_on_overflow(caplog):
    """When the queue is full, the oldest event is dropped."""
    bus = InProcessBus(ring_buffer_depth=2)
    received: list[str] = []

    # Slow handler ensures events pile up without being processed
    async def slow_handler(evt: SleipnirEvent) -> None:
        await asyncio.sleep(100)  # Never completes in normal test flow
        received.append(evt.event_id)

    await bus.subscribe(["*"], slow_handler)

    # Publish 3 events; the queue depth is 2 so e1 will be dropped
    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.in_process"):
        await bus.publish(make_event(event_id="e1"))
        await bus.publish(make_event(event_id="e2"))  # queue full after this
        await bus.publish(make_event(event_id="e3"))  # triggers overflow: e1 dropped

    assert any("Ring buffer overflow" in r.message for r in caplog.records)

    # Queue contains e2 and e3; e1 was dropped
    assert bus._subscriptions[0]._queue.qsize() == 2
    items: list[str] = []
    q = bus._subscriptions[0]._queue
    # Drain the queue to inspect remaining event IDs
    while not q.empty():
        items.append(q.get_nowait().event_id)
        q.task_done()
    assert "e1" not in items
    assert items == ["e2", "e3"]


async def test_ring_buffer_depth_default():
    bus = InProcessBus()
    assert bus._ring_buffer_depth == DEFAULT_RING_BUFFER_DEPTH


def test_ring_buffer_depth_zero_raises():
    with pytest.raises(ValueError, match="ring_buffer_depth must be >= 1"):
        InProcessBus(ring_buffer_depth=0)


def test_ring_buffer_depth_negative_raises():
    with pytest.raises(ValueError, match="ring_buffer_depth must be >= 1"):
        InProcessBus(ring_buffer_depth=-5)


async def test_ring_buffer_depth_custom():
    bus = InProcessBus(ring_buffer_depth=50)
    assert bus._ring_buffer_depth == 50

    async def handler(evt: SleipnirEvent) -> None:
        pass

    await bus.subscribe(["*"], handler)
    assert bus._subscriptions[0]._queue.maxsize == 50


# ---------------------------------------------------------------------------
# Flush
# ---------------------------------------------------------------------------


async def test_flush_waits_for_all_events():
    bus = InProcessBus()
    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)

    await bus.subscribe(["*"], handler)
    for i in range(20):
        await bus.publish(make_event(event_id=str(i)))

    await bus.flush()
    assert len(received) == 20


async def test_flush_with_no_subscriptions():
    bus = InProcessBus()
    await bus.flush()  # Must not raise


async def test_flush_with_multiple_subscribers():
    bus = InProcessBus()
    a: list[str] = []
    b: list[str] = []

    async def handler_a(evt: SleipnirEvent) -> None:
        a.append(evt.event_id)

    async def handler_b(evt: SleipnirEvent) -> None:
        b.append(evt.event_id)

    await bus.subscribe(["*"], handler_a)
    await bus.subscribe(["*"], handler_b)
    await bus.publish_batch([make_event(event_id=str(i)) for i in range(5)])
    await bus.flush()

    assert a == [str(i) for i in range(5)]
    assert b == [str(i) for i in range(5)]


def test_remove_subscription_silently_ignores_missing_sub():
    """_remove_subscription does not raise when the sub is not in the list."""
    from unittest.mock import MagicMock

    bus = InProcessBus()
    # No subscriptions registered — calling _remove_subscription should not raise.
    fake_sub = MagicMock()
    bus._remove_subscription(fake_sub)  # should not raise ValueError
