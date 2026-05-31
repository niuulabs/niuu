"""Integration tests for the RabbitMQ transport against a real broker.

Requires a running RabbitMQ server (default ``amqp://guest:guest@localhost:5672/``).
Set ``TEST_RABBITMQ_URL`` to override.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from sleipnir.adapters.rabbitmq import RabbitMQTransport
from sleipnir.domain.events import SleipnirEvent

from .conftest import RABBITMQ_URL, collect_events, make_event

pytestmark = pytest.mark.broker


# ---------------------------------------------------------------------------
# Basic pub/sub
# ---------------------------------------------------------------------------


async def test_publish_and_subscribe_round_trip(rabbitmq_transport: RabbitMQTransport):
    """A published event is received by a subscriber."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await rabbitmq_transport.subscribe(["ravn.*"], handler)
    event = make_event()
    await rabbitmq_transport.publish(event)

    await collect_events(1, received)

    assert len(received) == 1
    assert received[0].event_id == event.event_id
    assert received[0].event_type == event.event_type
    assert received[0].payload == event.payload


async def test_batch_publish_ordering(rabbitmq_transport: RabbitMQTransport):
    """Events published via publish_batch arrive in order."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await rabbitmq_transport.subscribe(["ravn.*"], handler)

    events = [make_event(summary=f"event-{i}") for i in range(5)]
    await rabbitmq_transport.publish_batch(events)

    await collect_events(5, received)

    assert [e.summary for e in received] == [f"event-{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Topic routing
# ---------------------------------------------------------------------------


async def test_topic_exchange_routing(rabbitmq_transport: RabbitMQTransport):
    """Subscriber with 'ravn.*' receives ravn events but not ting events."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await rabbitmq_transport.subscribe(["ravn.*"], handler)

    await rabbitmq_transport.publish(make_event(event_type="ravn.tool.complete"))
    await rabbitmq_transport.publish(make_event(event_type="ting.saga.created"))

    await collect_events(1, received, timeout=2.0)
    await asyncio.sleep(0.3)

    assert len(received) == 1
    assert received[0].event_type == "ravn.tool.complete"


# ---------------------------------------------------------------------------
# Durable queue
# ---------------------------------------------------------------------------


async def test_durable_queue_with_service_id():
    """A named service_id creates a durable queue that survives reconnection."""
    exchange = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    service_id = f"test-service-{uuid.uuid4().hex[:8]}"

    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    # First connection: subscribe with durable queue, then disconnect
    transport1 = RabbitMQTransport(
        url=RABBITMQ_URL,
        exchange_name=exchange,
        dead_letter_exchange=f"{exchange}_dlx",
        service_id=service_id,
    )
    async with transport1:
        await transport1.subscribe(["ravn.*"], handler)
    # transport1 is stopped; the durable queue remains on the broker

    # Publish an event while the subscriber is disconnected
    from sleipnir.adapters.rabbitmq import RabbitMQPublisher

    publisher = RabbitMQPublisher(url=RABBITMQ_URL, exchange_name=exchange)
    async with publisher:
        await publisher.publish(make_event(summary="while-offline"))

    # Second connection: same service_id should pick up the queued message
    transport2 = RabbitMQTransport(
        url=RABBITMQ_URL,
        exchange_name=exchange,
        dead_letter_exchange=f"{exchange}_dlx",
        service_id=service_id,
    )
    async with transport2:
        await transport2.subscribe(["ravn.*"], handler)
        await collect_events(1, received, timeout=15.0)

    assert len(received) == 1
    assert received[0].summary == "while-offline"


# ---------------------------------------------------------------------------
# Multiple subscribers
# ---------------------------------------------------------------------------


async def test_multiple_independent_subscribers(rabbitmq_transport: RabbitMQTransport):
    """Two subscribers each receive matching events independently."""
    ravn_received: list[SleipnirEvent] = []
    all_received: list[SleipnirEvent] = []

    async def ravn_handler(event: SleipnirEvent) -> None:
        ravn_received.append(event)

    async def all_handler(event: SleipnirEvent) -> None:
        all_received.append(event)

    await rabbitmq_transport.subscribe(["ravn.*"], ravn_handler)
    await rabbitmq_transport.subscribe(["*"], all_handler)

    await rabbitmq_transport.publish(make_event(event_type="ravn.tool.complete"))
    await rabbitmq_transport.publish(make_event(event_type="ting.saga.created"))

    await collect_events(1, ravn_received)
    await collect_events(2, all_received)

    assert len(ravn_received) == 1
    assert len(all_received) == 2


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------


async def test_unsubscribe_stops_delivery(rabbitmq_transport: RabbitMQTransport):
    """After unsubscribe, no more events are delivered."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    sub = await rabbitmq_transport.subscribe(["ravn.*"], handler)
    await rabbitmq_transport.publish(make_event(summary="before"))
    await collect_events(1, received)

    await sub.unsubscribe()
    await rabbitmq_transport.publish(make_event(summary="after"))
    await asyncio.sleep(0.5)

    assert len(received) == 1
    assert received[0].summary == "before"


# ---------------------------------------------------------------------------
# TTL filtering
# ---------------------------------------------------------------------------


async def test_ttl_zero_events_not_published(rabbitmq_transport: RabbitMQTransport):
    """Events with ttl=0 are dropped by the publisher."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await rabbitmq_transport.subscribe(["ravn.*"], handler)

    await rabbitmq_transport.publish(make_event(ttl=0, summary="expired"))
    await rabbitmq_transport.publish(make_event(ttl=300, summary="valid"))

    await collect_events(1, received)
    await asyncio.sleep(0.3)

    assert len(received) == 1
    assert received[0].summary == "valid"
