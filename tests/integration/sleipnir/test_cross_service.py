"""Cross-service integration tests: Volundr → Sleipnir → subscribers.

Validates the full event chain across service boundaries using real
broker transports.  Parameterised across NATS, RabbitMQ, and Redis.
"""

from __future__ import annotations

import uuid

import pytest

from sleipnir.domain.events import SleipnirEvent

from .conftest import (
    NATS_URL,
    RABBITMQ_URL,
    REDIS_URL,
    TEST_NATS_MAX_AGE_SECONDS,
    TEST_NATS_MAX_BYTES,
    collect_events,
    make_event,
)

pytestmark = pytest.mark.broker


# ---------------------------------------------------------------------------
# Mock channel for capturing Skuld broadcast output
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Transport factory (parameterised)
# ---------------------------------------------------------------------------


async def _make_nats():
    from sleipnir.adapters.nats_transport import NatsTransport

    name = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    return NatsTransport(
        servers=[NATS_URL],
        stream_name=name,
        subject_prefix=name,
        max_reconnect_attempts=3,
        connect_timeout_s=5.0,
        max_bytes=TEST_NATS_MAX_BYTES,
        max_age_seconds=TEST_NATS_MAX_AGE_SECONDS,
    )


async def _make_rabbitmq():
    from sleipnir.adapters.rabbitmq import RabbitMQTransport

    exchange = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    return RabbitMQTransport(
        url=RABBITMQ_URL,
        exchange_name=exchange,
        dead_letter_exchange=f"{exchange}_dlx",
    )


async def _make_redis():
    from sleipnir.adapters.redis_streams import RedisStreamsTransport

    prefix = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    return RedisStreamsTransport(url=REDIS_URL, stream_prefix=prefix)


@pytest.fixture(params=["nats", "rabbitmq", "redis"])
async def transport(request):
    """Yield a started transport for each broker backend."""
    factories = {
        "nats": _make_nats,
        "rabbitmq": _make_rabbitmq,
        "redis": _make_redis,
    }
    t = await factories[request.param]()
    async with t:
        yield t


# ---------------------------------------------------------------------------
# Volundr → Sleipnir → Skuld end-to-end
# ---------------------------------------------------------------------------


async def test_multi_publisher_single_subscriber(transport):
    """Events from two publishers are received by a single subscriber."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await transport.subscribe(["*"], handler)

    # Simulate Volundr publishing
    await transport.publish(
        make_event(
            event_type="volundr.session.started",
            source="volundr:event-sink",
            summary="from-volundr",
        )
    )
    # Simulate Ting publishing
    await transport.publish(
        make_event(
            event_type="ting.saga.created",
            source="ting:event-bridge",
            summary="from-ting",
        )
    )

    await collect_events(2, received)

    sources = {e.source for e in received}
    assert "volundr:event-sink" in sources
    assert "ting:event-bridge" in sources
