"""Tests for the Sleipnir test utilities (EventCapture, wait_for_event)."""

from __future__ import annotations

import asyncio

import pytest

from sleipnir.adapters.in_process import InProcessBus
from sleipnir.testing import EventCapture, wait_for_event
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# EventCapture
# ---------------------------------------------------------------------------


async def test_event_capture_collects_matching_events():
    bus = InProcessBus()

    async with EventCapture(bus, ["ravn.*"]) as capture:
        await bus.publish(make_event(event_type="ravn.tool.complete", event_id="e1"))
        await bus.publish(make_event(event_type="ravn.step.start", event_id="e2"))
        await bus.flush()

    assert len(capture.events) == 2
    assert {e.event_id for e in capture.events} == {"e1", "e2"}


async def test_event_capture_ignores_non_matching_events():
    bus = InProcessBus()

    async with EventCapture(bus, ["ravn.*"]) as capture:
        await bus.publish(make_event(event_type="ting.saga.created"))
        await bus.flush()

    assert capture.events == []


async def test_event_capture_events_returns_snapshot():
    bus = InProcessBus()

    async with EventCapture(bus, ["*"]) as capture:
        await bus.publish(make_event(event_id="e1"))
        await bus.flush()
        snapshot = capture.events
        await bus.publish(make_event(event_id="e2"))
        await bus.flush()

    # Snapshot is frozen — doesn't grow after capture
    assert len(snapshot) == 1
    assert len(capture.events) == 2


async def test_event_capture_unsubscribes_on_exit():
    bus = InProcessBus()

    async with EventCapture(bus, ["*"]) as capture:
        assert len(bus._subscriptions) == 1

    # After context exit the subscription is removed
    assert len(bus._subscriptions) == 0

    # No more events collected
    await bus.publish(make_event(event_id="late"))
    await asyncio.sleep(0)
    assert len(capture.events) == 0


async def test_event_capture_manual_start_stop():
    bus = InProcessBus()
    capture = EventCapture(bus, ["ravn.*"])

    await capture.start()
    await bus.publish(make_event(event_id="e1"))
    await bus.flush()
    await capture.stop()

    await bus.publish(make_event(event_id="e2"))
    await asyncio.sleep(0)

    assert len(capture.events) == 1
    assert capture.events[0].event_id == "e1"


async def test_event_capture_stop_is_idempotent():
    bus = InProcessBus()
    capture = EventCapture(bus, ["*"])

    await capture.start()
    await capture.stop()
    await capture.stop()  # Must not raise


async def test_event_capture_global_wildcard():
    bus = InProcessBus()

    async with EventCapture(bus, ["*"]) as capture:
        for et in ["ravn.tool.complete", "ting.saga.created", "volundr.pr.opened"]:
            await bus.publish(make_event(event_type=et))
        await bus.flush()

    assert len(capture.events) == 3


# ---------------------------------------------------------------------------
# wait_for_event
# ---------------------------------------------------------------------------


async def test_wait_for_event_returns_matching_event():
    bus = InProcessBus()

    async def publisher():
        await asyncio.sleep(0)
        await bus.publish(make_event(event_type="ravn.tool.complete", event_id="expected"))

    asyncio.create_task(publisher())
    event = await wait_for_event(bus, "ravn.*")

    assert event.event_id == "expected"
    assert event.event_type == "ravn.tool.complete"


async def test_wait_for_event_exact_match():
    bus = InProcessBus()

    async def publisher():
        await asyncio.sleep(0)
        await bus.publish(make_event(event_type="ravn.tool.complete"))

    asyncio.create_task(publisher())
    event = await wait_for_event(bus, "ravn.tool.complete")

    assert event.event_type == "ravn.tool.complete"


async def test_wait_for_event_global_wildcard():
    bus = InProcessBus()

    async def publisher():
        await asyncio.sleep(0)
        await bus.publish(make_event(event_type="ting.saga.created"))

    asyncio.create_task(publisher())
    event = await wait_for_event(bus, "*")

    assert event.event_type == "ting.saga.created"


async def test_wait_for_event_timeout_raises():
    bus = InProcessBus()

    with pytest.raises(asyncio.TimeoutError):
        await wait_for_event(bus, "ravn.*", timeout=0.05)


async def test_wait_for_event_unsubscribes_after_receipt():
    bus = InProcessBus()

    async def publisher():
        await asyncio.sleep(0)
        await bus.publish(make_event())

    asyncio.create_task(publisher())
    await wait_for_event(bus, "ravn.*")

    # wait_for_event should have cleaned up its subscription
    assert len(bus._subscriptions) == 0


async def test_wait_for_event_unsubscribes_on_timeout():
    bus = InProcessBus()

    with pytest.raises(asyncio.TimeoutError):
        await wait_for_event(bus, "ravn.*", timeout=0.05)

    # Subscription must be cleaned up even when timed out
    assert len(bus._subscriptions) == 0


async def test_wait_for_event_returns_first_matching_only():
    bus = InProcessBus()

    async def publisher():
        await asyncio.sleep(0)
        await bus.publish(make_event(event_id="first", event_type="ravn.tool.complete"))
        await bus.publish(make_event(event_id="second", event_type="ravn.tool.complete"))

    asyncio.create_task(publisher())
    event = await wait_for_event(bus, "ravn.*")

    assert event.event_id == "first"
