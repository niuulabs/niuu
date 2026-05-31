"""Integration tests for the Redis Streams transport against a real Redis.

Requires a running Redis server (default ``redis://localhost:6379``).
Set ``TEST_REDIS_URL`` to override.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from sleipnir.adapters.redis_streams import RedisStreamsTransport
from sleipnir.domain.events import SleipnirEvent

from .conftest import REDIS_URL, collect_events, make_event

pytestmark = pytest.mark.broker


# ---------------------------------------------------------------------------
# Basic pub/sub
# ---------------------------------------------------------------------------


async def test_publish_and_subscribe_round_trip(redis_transport: RedisStreamsTransport):
    """A published event is received by a subscriber."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await redis_transport.subscribe(["ravn.*"], handler)
    event = make_event()
    await redis_transport.publish(event)

    await collect_events(1, received)

    assert len(received) == 1
    assert received[0].event_id == event.event_id
    assert received[0].event_type == event.event_type
    assert received[0].payload == event.payload


async def test_batch_publish_ordering(redis_transport: RedisStreamsTransport):
    """Events published via publish_batch (pipeline) arrive in order."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await redis_transport.subscribe(["ravn.*"], handler)

    events = [make_event(summary=f"event-{i}") for i in range(5)]
    await redis_transport.publish_batch(events)

    await collect_events(5, received)

    assert [e.summary for e in received] == [f"event-{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Stream-per-namespace
# ---------------------------------------------------------------------------


async def test_stream_per_namespace_mapping(redis_transport: RedisStreamsTransport):
    """Events land in namespace-specific streams (sleipnir:ravn, sleipnir:ting)."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    # Subscribe to ravn only
    await redis_transport.subscribe(["ravn.*"], handler)

    await redis_transport.publish(make_event(event_type="ravn.tool.complete"))
    await redis_transport.publish(make_event(event_type="ting.saga.created"))

    await collect_events(1, received, timeout=2.0)
    await asyncio.sleep(0.3)

    assert len(received) == 1
    assert received[0].event_type == "ravn.tool.complete"


# ---------------------------------------------------------------------------
# Consumer group isolation
# ---------------------------------------------------------------------------


async def test_consumer_group_isolation(redis_transport: RedisStreamsTransport):
    """Two subscriptions each get all events (fan-out via unique groups)."""
    received_a: list[SleipnirEvent] = []
    received_b: list[SleipnirEvent] = []

    async def handler_a(event: SleipnirEvent) -> None:
        received_a.append(event)

    async def handler_b(event: SleipnirEvent) -> None:
        received_b.append(event)

    await redis_transport.subscribe(["ravn.*"], handler_a)
    await redis_transport.subscribe(["ravn.*"], handler_b)

    await redis_transport.publish(make_event(summary="shared-event"))

    await collect_events(1, received_a)
    await collect_events(1, received_b)

    assert len(received_a) == 1
    assert len(received_b) == 1
    assert received_a[0].event_id == received_b[0].event_id


# ---------------------------------------------------------------------------
# Replay on startup
# ---------------------------------------------------------------------------


async def test_replay_on_startup():
    """A subscriber with replay_on_startup=True receives historical events."""
    prefix = f"sleipnir_test_{uuid.uuid4().hex[:8]}"

    # Phase 1: publish events with replay disabled (default)
    transport1 = RedisStreamsTransport(url=REDIS_URL, stream_prefix=prefix)
    async with transport1:
        for i in range(3):
            await transport1.publish(make_event(summary=f"historical-{i}"))

    # Phase 2: new transport with replay enabled
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    transport2 = RedisStreamsTransport(
        url=REDIS_URL,
        stream_prefix=prefix,
        replay_on_startup=True,
    )
    async with transport2:
        await transport2.subscribe(["ravn.*"], handler)
        await collect_events(3, received)

    assert len(received) >= 3
    summaries = [e.summary for e in received[:3]]
    assert summaries == ["historical-0", "historical-1", "historical-2"]


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------


async def test_unsubscribe_stops_delivery(redis_transport: RedisStreamsTransport):
    """After unsubscribe, no more events are delivered."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    sub = await redis_transport.subscribe(["ravn.*"], handler)
    await redis_transport.publish(make_event(summary="before"))
    await collect_events(1, received)

    await sub.unsubscribe()
    await redis_transport.publish(make_event(summary="after"))
    await asyncio.sleep(0.5)

    assert len(received) == 1
    assert received[0].summary == "before"


# ---------------------------------------------------------------------------
# TTL filtering
# ---------------------------------------------------------------------------


async def test_ttl_zero_events_not_published(redis_transport: RedisStreamsTransport):
    """Events with ttl=0 are dropped by the publisher."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await redis_transport.subscribe(["ravn.*"], handler)

    await redis_transport.publish(make_event(ttl=0, summary="expired"))
    await redis_transport.publish(make_event(ttl=300, summary="valid"))

    await collect_events(1, received)
    await asyncio.sleep(0.3)

    assert len(received) == 1
    assert received[0].summary == "valid"
