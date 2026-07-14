"""Transport-neutral Sleipnir flock discovery tests."""

from niuu.mesh.identity import MeshIdentity
from ravn.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from sleipnir.adapters.in_process import InProcessBus


def _identity(peer_id: str, realm_id: str = "flock-a") -> MeshIdentity:
    return MeshIdentity(
        peer_id=peer_id,
        realm_id=realm_id,
        persona=peer_id,
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
