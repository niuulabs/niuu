"""Cross-service integration tests: Volundr → Sleipnir → Skuld.

Validates the full event chain across service boundaries using real
broker transports.  Parameterised across NATS, RabbitMQ, and Redis.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from skuld.channels import ChannelRegistry, MessageChannel
from skuld.sleipnir_bridge import SleipnirBridge
from sleipnir.domain.events import SleipnirEvent
from volundr.adapters.outbound.sleipnir_event_sink import SleipnirEventSink
from volundr.domain.models import SessionEvent, SessionEventType

from .conftest import NATS_URL, RABBITMQ_URL, REDIS_URL, collect_events, make_event

pytestmark = pytest.mark.broker


# ---------------------------------------------------------------------------
# Mock channel for capturing Skuld broadcast output
# ---------------------------------------------------------------------------


class _CaptureChannel(MessageChannel):
    """In-memory channel that records all broadcast events."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._open = True

    async def send_event(self, event: dict) -> None:
        self.events.append(event)

    @property
    def channel_type(self) -> str:
        return "test"

    @property
    def is_open(self) -> bool:
        return self._open

    async def close(self) -> None:
        self._open = False


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


async def test_volundr_sink_to_skuld_bridge(transport):
    """SleipnirEventSink publishes → real broker → SleipnirBridge → channel."""
    session_id = uuid4()
    channel = _CaptureChannel()
    registry = ChannelRegistry()
    registry.add(channel)

    # Set up Skuld bridge on the subscriber side
    bridge = SleipnirBridge(
        subscriber=transport,
        registry=registry,
        session_id=str(session_id),
        event_patterns=["volundr.*"],
    )
    await bridge.start()

    # Set up Volundr sink on the publisher side
    sink = SleipnirEventSink(publisher=transport)

    session_event = SessionEvent(
        id=uuid4(),
        session_id=session_id,
        event_type=SessionEventType.SESSION_START,
        timestamp=datetime.now(UTC),
        data={"model": "claude-sonnet-4-6", "repo": "niuu/volundr", "branch": "main"},
        sequence=1,
    )

    await sink.emit(session_event)

    # Wait for the event to flow through the broker
    deadline = asyncio.get_event_loop().time() + 5.0
    while not channel.events:
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.05)

    await bridge.stop()

    assert len(channel.events) == 1
    wire = channel.events[0]
    assert wire["type"] == "sleipnir"
    assert wire["event_type"] == "volundr.session.started"
    assert wire["correlation_id"] == str(session_id)
    assert wire["payload"]["session_id"] == str(session_id)
    assert wire["payload"]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# correlation_id filtering
# ---------------------------------------------------------------------------


async def test_correlation_id_filtering(transport):
    """Skuld bridge only forwards events matching its session_id."""
    target_session = str(uuid4())
    other_session = str(uuid4())

    channel = _CaptureChannel()
    registry = ChannelRegistry()
    registry.add(channel)

    bridge = SleipnirBridge(
        subscriber=transport,
        registry=registry,
        session_id=target_session,
        event_patterns=["volundr.*"],
    )
    await bridge.start()

    # Publish events for two different sessions
    await transport.publish(
        make_event(
            event_type="volundr.session.started",
            correlation_id=target_session,
            payload={"session_id": target_session},
            summary="target session",
        )
    )
    await transport.publish(
        make_event(
            event_type="volundr.session.started",
            correlation_id=other_session,
            payload={"session_id": other_session},
            summary="other session",
        )
    )

    deadline = asyncio.get_event_loop().time() + 3.0
    while not channel.events:
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.05)
    # Extra wait to confirm second event doesn't arrive
    await asyncio.sleep(0.5)

    await bridge.stop()

    assert len(channel.events) == 1
    assert channel.events[0]["correlation_id"] == target_session


# ---------------------------------------------------------------------------
# Multi-publisher
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
