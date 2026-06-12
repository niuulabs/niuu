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


def _nats_transport(
    stream_name: str,
    *,
    consumer_group: str | None = None,
    replay_from_sequence: int | None = None,
) -> NatsTransport:
    return NatsTransport(
        servers=[NATS_URL],
        stream_name=stream_name,
        subject_prefix=stream_name,
        consumer_group=consumer_group,
        replay_from_sequence=replay_from_sequence,
        max_reconnect_attempts=3,
        connect_timeout_s=5.0,
    )


def _mesh_peer(
    peer_id: str,
    stream_name: str,
    *,
    environment_id: str = "local-dev",
    replay_from_sequence: int | None = None,
) -> SleipnirMeshAdapter:
    transport = _nats_transport(stream_name, replay_from_sequence=replay_from_sequence)
    return SleipnirMeshAdapter(
        publisher=transport,
        subscriber=transport,
        own_peer_id=peer_id,
        environment_id=environment_id,
    )


async def _collect_ravn_events(
    count: int,
    received: list[RavnEvent],
    timeout: float = 5.0,
) -> list[RavnEvent]:
    deadline = asyncio.get_event_loop().time() + timeout
    while len(received) < count:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        await asyncio.sleep(0.05)
    return list(received)


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
    peer_a = _mesh_peer("valkyrie-a", stream_name)
    peer_b = _mesh_peer("valkyrie-b", stream_name)
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
            root_correlation_id="root-env-signal-1",
        )

        await peer_a.publish(event, "signal.kubernetes.pod")
        await collect_events(1, received)

        assert len(received) == 1
        assert received[0].payload == event.payload
        assert received[0].correlation_id == "env-signal-1"
        assert received[0].root_correlation_id == "root-env-signal-1"

        replay_peer = _mesh_peer("valkyrie-replay", stream_name, replay_from_sequence=1)
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


async def test_resident_signal_state_judgment_action_flow_over_nats():
    """Resident Valkyries process signal, state, judgment, and action over NATS mesh."""
    stream_name = f"ravn_resident_flow_{uuid.uuid4().hex[:8]}"
    signal_source = _mesh_peer("k8s-watcher", stream_name, environment_id="cluster-a")
    valkyrie = _mesh_peer("k8s-valkyrie", stream_name, environment_id="cluster-a")
    action_runner = _mesh_peer("action-runner", stream_name, environment_id="cluster-a")
    observer = _mesh_peer("odin-observer", stream_name, environment_id="cluster-a")

    signal_events: list[RavnEvent] = []
    state_events: list[RavnEvent] = []
    judgment_events: list[RavnEvent] = []
    action_events: list[RavnEvent] = []

    async def action_handler(message: dict) -> dict:
        return {
            "status": "accepted",
            "action": message["action"],
            "target": message["target"],
            "correlation_id": message["correlation_id"],
        }

    async def signal_handler(event: RavnEvent) -> None:
        signal_events.append(event)
        common = {
            "source": "k8s-valkyrie",
            "timestamp": datetime.now(UTC),
            "urgency": event.urgency,
            "correlation_id": event.correlation_id,
            "session_id": event.session_id,
            "root_correlation_id": event.root_correlation_id,
        }
        await valkyrie.publish(
            RavnEvent(
                type=RavnEventType.TASK_STARTED,
                payload={
                    "environment": "cluster-a",
                    "state": "investigating",
                    "signal": event.payload,
                },
                **common,
            ),
            "environment.state",
        )
        await valkyrie.publish(
            RavnEvent(
                type=RavnEventType.DECISION,
                payload={
                    "attention": "watch",
                    "judgment": "pod restart requires inspection",
                    "confidence": 0.84,
                },
                **common,
            ),
            "valkyrie.judgment",
        )
        reply = await valkyrie.send(
            "action-runner",
            {
                "action": "inspect_pod",
                "target": event.payload["pod"],
                "correlation_id": event.correlation_id,
            },
            timeout_s=2.0,
        )
        await valkyrie.publish(
            RavnEvent(
                type=RavnEventType.TOOL_RESULT,
                payload={"action_reply": reply},
                **common,
            ),
            "valkyrie.action.result",
        )

    async def state_handler(event: RavnEvent) -> None:
        state_events.append(event)

    async def judgment_handler(event: RavnEvent) -> None:
        judgment_events.append(event)

    async def action_result_handler(event: RavnEvent) -> None:
        action_events.append(event)

    action_runner.set_rpc_handler(action_handler)

    for peer in (signal_source, valkyrie, action_runner, observer):
        await peer.start()
    try:
        await valkyrie.subscribe("signal.kubernetes.pod", signal_handler)
        await observer.subscribe("environment.state", state_handler)
        await observer.subscribe("valkyrie.judgment", judgment_handler)
        await observer.subscribe("valkyrie.action.result", action_result_handler)

        await signal_source.publish(
            RavnEvent(
                type=RavnEventType.DECISION,
                source="k8s-watcher",
                payload={"kind": "Pod", "pod": "api-7d9", "reason": "Restarted"},
                timestamp=datetime.now(UTC),
                urgency=0.8,
                correlation_id="corr-cluster-a-1",
                session_id="cluster-a-session",
                root_correlation_id="root-cluster-a-1",
            ),
            "signal.kubernetes.pod",
        )

        await _collect_ravn_events(1, signal_events)
        await _collect_ravn_events(1, state_events)
        await _collect_ravn_events(1, judgment_events)
        await _collect_ravn_events(1, action_events)

        assert signal_events[0].payload["pod"] == "api-7d9"
        assert state_events[0].payload["state"] == "investigating"
        assert judgment_events[0].payload["attention"] == "watch"
        assert action_events[0].payload["action_reply"] == {
            "status": "accepted",
            "action": "inspect_pod",
            "target": "api-7d9",
            "correlation_id": "corr-cluster-a-1",
        }
        for event in [*state_events, *judgment_events, *action_events]:
            assert event.correlation_id == "corr-cluster-a-1"
            assert event.root_correlation_id == "root-cluster-a-1"
    finally:
        for peer in (observer, action_runner, valkyrie, signal_source):
            await peer.stop()


async def test_nats_consumer_group_delivers_resident_signals_once():
    """A resident consumer group load-shares signals without duplicate delivery."""
    stream_name = f"ravn_resident_group_{uuid.uuid4().hex[:8]}"
    publisher = _nats_transport(stream_name)
    worker_a = _nats_transport(stream_name, consumer_group="resident-workers")
    worker_b = _nats_transport(stream_name, consumer_group="resident-workers")
    received_by_a: list[SleipnirEvent] = []
    received_by_b: list[SleipnirEvent] = []

    async def handler_a(event: SleipnirEvent) -> None:
        received_by_a.append(event)

    async def handler_b(event: SleipnirEvent) -> None:
        received_by_b.append(event)

    async def collect_total(count: int) -> list[SleipnirEvent]:
        deadline = asyncio.get_event_loop().time() + 5.0
        while len(received_by_a) + len(received_by_b) < count:
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.05)
        return [*received_by_a, *received_by_b]

    async with publisher, worker_a, worker_b:
        await worker_a.subscribe(["ravn.mesh.signal.kubernetes.pod"], handler_a)
        await worker_b.subscribe(["ravn.mesh.signal.kubernetes.pod"], handler_b)

        events = [
            make_event(
                event_id=f"resident-signal-{idx}",
                event_type="ravn.mesh.signal.kubernetes.pod",
                payload={"pod": f"api-{idx}", "reason": "Restarted"},
                correlation_id=f"corr-group-{idx}",
            )
            for idx in range(4)
        ]
        for event in events:
            await publisher.publish(event)

        delivered = await collect_total(4)

    delivered_ids = [event.event_id for event in delivered]
    assert set(delivered_ids) == {event.event_id for event in events}
    assert len(delivered_ids) == len(set(delivered_ids))


async def test_nats_consumer_group_keeps_distinct_subscription_filters():
    """RPC and event subscriptions in one group must not share one durable filter."""
    stream_name = f"ravn_multi_filter_{uuid.uuid4().hex[:8]}"
    transport = _nats_transport(stream_name, consumer_group="resident-workers")
    rpc_received: list[SleipnirEvent] = []
    judgment_received: list[SleipnirEvent] = []

    async def rpc_handler(event: SleipnirEvent) -> None:
        rpc_received.append(event)

    async def judgment_handler(event: SleipnirEvent) -> None:
        judgment_received.append(event)

    async with transport:
        await transport.subscribe(["ravn.mesh.rpc.valkyrie_a"], rpc_handler)
        await transport.subscribe(["ravn.mesh.valkyrie.judgment.proposed"], judgment_handler)

        await transport.publish(
            make_event(
                event_id="rpc-event",
                event_type="ravn.mesh.rpc.valkyrie_a",
                payload={"kind": "rpc"},
            )
        )
        await transport.publish(
            make_event(
                event_id="judgment-event",
                event_type="ravn.mesh.valkyrie.judgment.proposed",
                payload={"kind": "judgment"},
            )
        )

        await collect_events(1, rpc_received)
        await collect_events(1, judgment_received)

    assert [event.event_id for event in rpc_received] == ["rpc-event"]
    assert [event.event_id for event in judgment_received] == ["judgment-event"]
