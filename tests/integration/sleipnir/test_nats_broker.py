"""Integration tests for the NATS JetStream transport against a real broker.

Requires a running NATS server with JetStream enabled (``nats-server --jetstream``).
Set ``TEST_NATS_URL`` to override the default ``nats://localhost:4222``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.domain.events import RavnEvent, RavnEventType
from sleipnir.adapters.nats_transport import NatsTransport
from sleipnir.domain.events import SleipnirEvent

from .conftest import NATS_URL, collect_events, make_event

pytestmark = pytest.mark.broker


# ---------------------------------------------------------------------------
# Basic pub/sub
# ---------------------------------------------------------------------------


async def test_publish_and_subscribe_round_trip(nats_transport: NatsTransport):
    """A published event is received by a subscriber."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await nats_transport.subscribe(["ravn.*"], handler)
    event = make_event()
    await nats_transport.publish(event)

    await collect_events(1, received)

    assert len(received) == 1
    assert received[0].event_id == event.event_id
    assert received[0].event_type == event.event_type
    assert received[0].payload == event.payload


async def test_batch_publish_preserves_order(nats_transport: NatsTransport):
    """Events published via publish_batch arrive in order."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await nats_transport.subscribe(["ravn.*"], handler)

    events = [make_event(summary=f"event-{i}") for i in range(5)]
    await nats_transport.publish_batch(events)

    await collect_events(5, received)

    assert [e.summary for e in received] == [f"event-{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


async def test_wildcard_pattern_matching(nats_transport: NatsTransport):
    """Subscriber with 'ravn.*' receives ravn events but not ting events."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await nats_transport.subscribe(["ravn.*"], handler)

    await nats_transport.publish(make_event(event_type="ravn.tool.complete"))
    await nats_transport.publish(make_event(event_type="ting.saga.created"))

    await collect_events(1, received, timeout=2.0)
    # Give a moment for the ting event to *not* arrive
    await asyncio.sleep(0.3)

    assert len(received) == 1
    assert received[0].event_type == "ravn.tool.complete"


# ---------------------------------------------------------------------------
# Multiple subscribers
# ---------------------------------------------------------------------------


async def test_multiple_subscribers_receive_independently(nats_transport: NatsTransport):
    """Two subscribers to different patterns each receive matching events."""
    ravn_received: list[SleipnirEvent] = []
    ting_received: list[SleipnirEvent] = []

    async def ravn_handler(event: SleipnirEvent) -> None:
        ravn_received.append(event)

    async def ting_handler(event: SleipnirEvent) -> None:
        ting_received.append(event)

    await nats_transport.subscribe(["ravn.*"], ravn_handler)
    await nats_transport.subscribe(["ting.*"], ting_handler)

    await nats_transport.publish(make_event(event_type="ravn.tool.complete"))
    await nats_transport.publish(make_event(event_type="ting.saga.created"))

    await collect_events(1, ravn_received)
    await collect_events(1, ting_received)

    assert len(ravn_received) == 1
    assert len(ting_received) == 1


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------


async def test_unsubscribe_stops_delivery(nats_transport: NatsTransport):
    """After unsubscribe, no more events are delivered."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    sub = await nats_transport.subscribe(["ravn.*"], handler)
    await nats_transport.publish(make_event(summary="before"))
    await collect_events(1, received)

    await sub.unsubscribe()
    await nats_transport.publish(make_event(summary="after"))
    await asyncio.sleep(0.5)

    assert len(received) == 1
    assert received[0].summary == "before"


# ---------------------------------------------------------------------------
# Field preservation
# ---------------------------------------------------------------------------


async def test_correlation_id_and_urgency_preserved(nats_transport: NatsTransport):
    """correlation_id and urgency survive the NATS round-trip."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await nats_transport.subscribe(["ravn.*"], handler)
    event = make_event(correlation_id="session-xyz", urgency=0.9)
    await nats_transport.publish(event)

    await collect_events(1, received)

    assert received[0].correlation_id == "session-xyz"
    assert received[0].urgency == 0.9


# ---------------------------------------------------------------------------
# TTL filtering
# ---------------------------------------------------------------------------


async def test_ttl_zero_events_not_delivered(nats_transport: NatsTransport):
    """Events with ttl=0 are dropped by the publisher."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await nats_transport.subscribe(["ravn.*"], handler)

    await nats_transport.publish(make_event(ttl=0, summary="expired"))
    await nats_transport.publish(make_event(ttl=300, summary="valid"))

    await collect_events(1, received)
    await asyncio.sleep(0.3)

    assert len(received) == 1
    assert received[0].summary == "valid"


# ---------------------------------------------------------------------------
# Replay from sequence
# ---------------------------------------------------------------------------


async def test_replay_from_sequence(nats_transport: NatsTransport):
    """A new subscriber with replay_from_sequence receives historical events."""
    # Publish 3 events before subscribing
    events = [make_event(summary=f"event-{i}") for i in range(3)]
    for event in events:
        await nats_transport.publish(event)

    # Allow events to be persisted
    await asyncio.sleep(0.3)

    # Create a new subscriber that replays from the beginning
    stream_name = nats_transport._publisher._stream_name
    subject_prefix = nats_transport._publisher._subject_prefix

    replay_sub = NatsTransport(
        servers=[NATS_URL],
        stream_name=stream_name,
        subject_prefix=subject_prefix,
        replay_from_sequence=1,
        max_reconnect_attempts=3,
        connect_timeout_s=5.0,
    )

    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with replay_sub:
        await replay_sub.subscribe(["ravn.*"], handler)
        await collect_events(3, received)

    assert len(received) >= 3
    summaries = [e.summary for e in received[:3]]
    assert summaries == ["event-0", "event-1", "event-2"]


async def test_two_sleipnir_mesh_peers_over_nats_publish_and_replay():
    """Two SleipnirMeshAdapter peers exchange and replay environment events over NATS."""
    stream_name = f"ravn_environment_test_{uuid.uuid4().hex[:8]}"
    subject_prefix = stream_name
    peer_a_transport = NatsTransport(
        servers=[NATS_URL],
        stream_name=stream_name,
        subject_prefix=subject_prefix,
        max_reconnect_attempts=3,
        connect_timeout_s=5.0,
    )
    peer_b_transport = NatsTransport(
        servers=[NATS_URL],
        stream_name=stream_name,
        subject_prefix=subject_prefix,
        max_reconnect_attempts=3,
        connect_timeout_s=5.0,
    )

    peer_a = SleipnirMeshAdapter(
        publisher=peer_a_transport,
        subscriber=peer_a_transport,
        own_peer_id="valkyrie-a",
    )
    peer_b = SleipnirMeshAdapter(
        publisher=peer_b_transport,
        subscriber=peer_b_transport,
        own_peer_id="valkyrie-b",
    )
    received: list[RavnEvent] = []

    async def handler(event: RavnEvent) -> None:
        received.append(event)

    await peer_a.start()
    await peer_b.start()
    try:
        await peer_b.subscribe("signal.kubernetes.pod", handler)
        event = RavnEvent(
            type=RavnEventType.DECISION,
            source="valkyrie-a",
            payload={"signal": "pod/restarted", "environment": "local-dev"},
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id="env-signal-1",
            session_id="environment-nats",
        )

        await peer_a.publish(event, "signal.kubernetes.pod")
        await collect_events(1, received)

        assert len(received) == 1
        assert received[0].payload == event.payload
        assert received[0].correlation_id == "env-signal-1"

        replay_transport = NatsTransport(
            servers=[NATS_URL],
            stream_name=stream_name,
            subject_prefix=subject_prefix,
            replay_from_sequence=1,
            max_reconnect_attempts=3,
            connect_timeout_s=5.0,
        )
        replay_peer = SleipnirMeshAdapter(
            publisher=replay_transport,
            subscriber=replay_transport,
            own_peer_id="valkyrie-replay",
        )
        replayed: list[RavnEvent] = []

        async def replay_handler(event: RavnEvent) -> None:
            replayed.append(event)

        await replay_peer.start()
        try:
            await replay_peer.subscribe("signal.kubernetes.pod", replay_handler)
            await collect_events(1, replayed)
        finally:
            await replay_peer.stop()

        assert replayed
        assert any(item.correlation_id == "env-signal-1" for item in replayed)
    finally:
        await peer_b.stop()
        await peer_a.stop()
