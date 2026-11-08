"""Transport-neutral Sleipnir flock discovery tests."""

from niuu.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from niuu.mesh.identity import MeshIdentity
from sleipnir.adapters.in_process import InProcessBus


def _identity(peer_id: str, realm_id: str = "flock-a") -> MeshIdentity:
    return MeshIdentity(
        peer_id=peer_id,
        realm_id=realm_id,
        persona=peer_id,
        display_name=f"Name {peer_id}",
        participant_type="skuld" if peer_id == "second" else "ravn",
        capabilities=["chat"],
        permission_mode="permissive",
        version="test",
        consumes_event_types=["code.changed"],
        emits_event_types=["review.completed"],
    )


async def test_event_bus_discovery_converges_immediately_and_handles_leave() -> None:
    bus = InProcessBus()
    first = EventBusDiscoveryAdapter(_identity("first"), bus, bus, manage_transport_lifecycle=False)
    second = EventBusDiscoveryAdapter(
        _identity("second"), bus, bus, manage_transport_lifecycle=False
    )

    await first.start()
    await second.start()
    await bus.flush()

    assert set(first.peers()) == {"second"}
    assert set(second.peers()) == {"first"}
    assert first.peers()["second"].display_name == "Name second"
    assert first.peers()["second"].participant_type == "skuld"
    assert second.peers()["first"].display_name == "Name first"
    assert second.peers()["first"].capabilities == ["chat"]
    assert second.peers()["first"].consumes_event_types == ["code.changed"]
    assert second.peers()["first"].emits_event_types == ["review.completed"]

    first._identity.consumes_event_types = ["review.completed"]
    first._identity.emits_event_types = ["release.ready"]
    await first.announce()
    await bus.flush()
    assert second.peers()["first"].consumes_event_types == ["review.completed"]
    assert second.peers()["first"].emits_event_types == ["release.ready"]

    await second.stop()
    await bus.flush()
    assert first.peers() == {}
    await first.stop()


async def test_event_bus_discovery_ignores_other_realms() -> None:
    bus = InProcessBus()
    first = EventBusDiscoveryAdapter(_identity("first"), bus, bus, manage_transport_lifecycle=False)
    outsider = EventBusDiscoveryAdapter(
        _identity("outsider", "flock-b"), bus, bus, manage_transport_lifecycle=False
    )

    await first.start()
    await outsider.start()
    await bus.flush()

    assert first._announce_event_type == "ravn.mesh.realm_flock_a.announce"
    assert outsider._announce_event_type == "ravn.mesh.realm_flock_b.announce"
    assert first.peers() == {}
    assert outsider.peers() == {}
    await outsider.stop()
    await first.stop()


async def test_a_choosers_facts_round_trip_and_follow_the_participants_activity() -> None:
    bus = InProcessBus()
    reviewer = _identity("reviewer")
    reviewer.role = "reviewer"
    reviewer.description = "Reviews pull requests for correctness."
    reviewer.activity_state = "idle"
    watcher = EventBusDiscoveryAdapter(
        _identity("watcher"), bus, bus, manage_transport_lifecycle=False
    )
    announcer = EventBusDiscoveryAdapter(reviewer, bus, bus, manage_transport_lifecycle=False)
    await watcher.start()
    await announcer.start()
    await bus.flush()

    peer = watcher.peers()["reviewer"]
    assert (peer.role, peer.description) == ("reviewer", "Reviews pull requests for correctness.")
    assert (peer.activity_state, peer.status) == ("idle", "idle")

    # The participant keeps its identity current; the next heartbeat carries it.
    reviewer.activity_state = "awaiting_input"
    reviewer.busy = True
    await announcer._publish("heartbeat")
    await bus.flush()

    peer = watcher.peers()["reviewer"]
    assert (peer.activity_state, peer.status) == ("awaiting_input", "busy")
    await announcer.stop()
    await watcher.stop()


async def test_an_announcement_from_before_the_new_fields_still_parses() -> None:
    from datetime import UTC, datetime

    from sleipnir.domain.events import SleipnirEvent

    bus = InProcessBus()
    watcher = EventBusDiscoveryAdapter(
        _identity("watcher"), bus, bus, manage_transport_lifecycle=False
    )
    await watcher.start()
    older = {
        "peer_id": "old-ravn",
        "realm_id": "flock-a",
        "persona": "coder",
        "capabilities": ["code"],
        "permission_mode": "full_access",
        "version": "0.1.0",
    }
    newer = {**older, "peer_id": "new-ravn", "role": "planner", "some_future_field": {"a": 1}}
    for identity in (older, newer):
        await bus.publish(
            SleipnirEvent(
                event_type=watcher._announce_event_type,
                source=f"ravn:{identity['peer_id']}",
                payload={"identity": identity, "action": "join", "status": "idle"},
                summary="peer join",
                domain="code",
                urgency=0.3,
                timestamp=datetime.now(UTC),
            )
        )
    await bus.flush()

    peers = watcher.peers()
    assert (peers["old-ravn"].role, peers["old-ravn"].description) == ("", "")
    assert peers["old-ravn"].activity_state == "" and peers["old-ravn"].persona == "coder"
    assert peers["new-ravn"].role == "planner"
    await watcher.stop()
