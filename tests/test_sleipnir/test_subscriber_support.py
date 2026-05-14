"""Tests for shared subscriber support helpers.

Covers :func:`dispatch_to_subscriptions` and the ``DEFAULT_RING_BUFFER_DEPTH``
constant introduced by the code-review refactor (NIU-523).
"""

from __future__ import annotations

import asyncio
import logging

from sleipnir.adapters._subscriber_support import (
    DEFAULT_RING_BUFFER_DEPTH,
    _BaseSubscription,
    consume_queue,
    dispatch_to_subscriptions,
)
from sleipnir.domain.events import SleipnirEvent
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sub(patterns: list[str], handler) -> tuple[_BaseSubscription, asyncio.Task]:
    """Create a live _BaseSubscription with a running consumer task."""
    queue: asyncio.Queue[SleipnirEvent] = asyncio.Queue(maxsize=DEFAULT_RING_BUFFER_DEPTH)
    task = asyncio.create_task(consume_queue(queue, handler))
    sub = _BaseSubscription(patterns, queue, task, lambda: None)
    return sub, task


# ---------------------------------------------------------------------------
# Unit tests — DEFAULT_RING_BUFFER_DEPTH
# ---------------------------------------------------------------------------


def test_default_ring_buffer_depth_is_positive():
    assert DEFAULT_RING_BUFFER_DEPTH > 0


def test_default_ring_buffer_depth_consistent_across_adapters():
    """All three adapters expose the same value as _subscriber_support."""
    import sleipnir.adapters.in_process as ip_mod
    import sleipnir.adapters.nng_transport as nng_mod
    import sleipnir.adapters.rabbitmq as rmq_mod

    assert ip_mod.DEFAULT_RING_BUFFER_DEPTH == DEFAULT_RING_BUFFER_DEPTH
    assert nng_mod.DEFAULT_RING_BUFFER_DEPTH == DEFAULT_RING_BUFFER_DEPTH
    assert rmq_mod.DEFAULT_RING_BUFFER_DEPTH == DEFAULT_RING_BUFFER_DEPTH


# ---------------------------------------------------------------------------
# Unit tests — dispatch_to_subscriptions
# ---------------------------------------------------------------------------


async def test_dispatch_delivers_to_matching_subscription():
    """Event matching subscription pattern is enqueued."""
    received: list[SleipnirEvent] = []
    done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        done.set()

    sub, task = _make_sub(["ravn.*"], handler)
    try:
        event = make_event(event_id="d-1", event_type="ravn.tool.complete")
        await dispatch_to_subscriptions(
            event, [sub], DEFAULT_RING_BUFFER_DEPTH, logging.getLogger(__name__)
        )
        await asyncio.wait_for(done.wait(), timeout=2.0)
    finally:
        await sub.unsubscribe()

    assert len(received) == 1
    assert received[0].event_id == "d-1"


async def test_dispatch_skips_non_matching_subscription():
    """Event not matching subscription pattern is not delivered."""
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    sub, _ = _make_sub(["ting.*"], handler)
    try:
        event = make_event(event_type="ravn.tool.complete")
        await dispatch_to_subscriptions(
            event, [sub], DEFAULT_RING_BUFFER_DEPTH, logging.getLogger(__name__)
        )
        await asyncio.sleep(0.05)
    finally:
        await sub.unsubscribe()

    assert received == []


async def test_dispatch_skips_inactive_subscription():
    """Inactive (unsubscribed) subscriptions are not dispatched to."""
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    sub, _ = _make_sub(["*"], handler)
    await sub.unsubscribe()  # deactivate before dispatching

    event = make_event(event_id="inactive")
    await dispatch_to_subscriptions(
        event, [sub], DEFAULT_RING_BUFFER_DEPTH, logging.getLogger(__name__)
    )
    await asyncio.sleep(0.05)

    assert received == []


async def test_dispatch_drops_ttl_zero_event(caplog):
    """Events with ttl=0 are dropped with a DEBUG log; nothing is enqueued."""
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    sub, _ = _make_sub(["*"], handler)
    try:
        event = make_event(ttl=0)
        log = logging.getLogger("sleipnir.adapters._subscriber_support")
        with caplog.at_level(logging.DEBUG, logger=log.name):
            await dispatch_to_subscriptions(event, [sub], DEFAULT_RING_BUFFER_DEPTH, log)
        await asyncio.sleep(0.05)
    finally:
        await sub.unsubscribe()

    assert received == []
    assert any("ttl" in r.message.lower() or "expired" in r.message.lower() for r in caplog.records)


async def test_dispatch_delivers_to_multiple_subscriptions():
    """All matching subscriptions independently receive the event."""
    bucket_a: list[str] = []
    bucket_b: list[str] = []
    done_a = asyncio.Event()
    done_b = asyncio.Event()

    async def handler_a(evt: SleipnirEvent) -> None:
        bucket_a.append(evt.event_id)
        done_a.set()

    async def handler_b(evt: SleipnirEvent) -> None:
        bucket_b.append(evt.event_id)
        done_b.set()

    sub_a, _ = _make_sub(["*"], handler_a)
    sub_b, _ = _make_sub(["*"], handler_b)
    try:
        event = make_event(event_id="multi")
        await dispatch_to_subscriptions(
            event, [sub_a, sub_b], DEFAULT_RING_BUFFER_DEPTH, logging.getLogger(__name__)
        )
        await asyncio.wait_for(asyncio.gather(done_a.wait(), done_b.wait()), timeout=2.0)
    finally:
        await sub_a.unsubscribe()
        await sub_b.unsubscribe()

    assert bucket_a == ["multi"]
    assert bucket_b == ["multi"]


async def test_dispatch_star_pattern_matches_all_namespaces():
    """'*' pattern dispatches events from any namespace."""
    received_types: list[str] = []
    all_done = asyncio.Event()
    target = ["ravn.tool.complete", "ting.task.started", "volundr.pr.opened"]

    async def handler(evt: SleipnirEvent) -> None:
        received_types.append(evt.event_type)
        if all(t in received_types for t in target):
            all_done.set()

    sub, _ = _make_sub(["*"], handler)
    try:
        for et in target:
            await dispatch_to_subscriptions(
                make_event(event_type=et),
                [sub],
                DEFAULT_RING_BUFFER_DEPTH,
                logging.getLogger(__name__),
            )
        await asyncio.wait_for(all_done.wait(), timeout=2.0)
    finally:
        await sub.unsubscribe()

    assert sorted(received_types) == sorted(target)


async def test_dispatch_empty_subscription_list_is_noop():
    """Dispatching to an empty list does nothing and does not raise."""
    event = make_event()
    await dispatch_to_subscriptions(
        event, [], DEFAULT_RING_BUFFER_DEPTH, logging.getLogger(__name__)
    )


async def test_dispatch_ring_buffer_overflow_warns(caplog):
    """When the queue is full, dispatch drops the oldest event with a WARNING."""
    released = asyncio.Event()

    async def blocking_handler(_: SleipnirEvent) -> None:
        await released.wait()

    depth = 2
    queue: asyncio.Queue[SleipnirEvent] = asyncio.Queue(maxsize=depth)
    task = asyncio.create_task(consume_queue(queue, blocking_handler))
    sub = _BaseSubscription(["*"], queue, task, lambda: None)

    log = logging.getLogger("sleipnir.adapters._subscriber_support")
    try:
        with caplog.at_level(logging.WARNING, logger=log.name):
            for i in range(depth + 3):
                await dispatch_to_subscriptions(make_event(event_id=f"ov-{i}"), [sub], depth, log)
        overflow_warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
        assert len(overflow_warnings) >= 1
    finally:
        released.set()
        await sub.unsubscribe()
