"""The mesh's wire prefix is `mesh`, and nothing speaks the old `ravn.mesh` name."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from niuu.adapters.discovery import sleipnir as amqp_discovery
from niuu.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.collaboration import observation_relay
from niuu.mesh import mesh_event_prefix
from niuu.mesh.config import MeshSleipnirConfig
from niuu.mesh.identity import MeshIdentity
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import EVENT_NAMESPACES, SleipnirEvent, validate_event_type
from sleipnir.domain.registry import MESH_ANNOUNCE, known_event_types

SRC = Path(__file__).resolve().parents[3] / "src"
OLD = "ravn" + ".mesh"  # spelled apart so this file does not contain the old name


def test_the_prefix_is_the_name_of_the_mesh_event_type_and_a_realm_scopes_it() -> None:
    assert mesh_event_prefix("") == "mesh"
    assert mesh_event_prefix("Flock-A") == "mesh.realm_flock_a"


def test_every_leg_of_the_mesh_rides_the_new_prefix() -> None:
    bus = InProcessBus()
    mesh = SleipnirMeshAdapter(publisher=bus, subscriber=bus, own_peer_id="p", rpc_timeout_s=1.0)
    identity = MeshIdentity(
        peer_id="p",
        realm_id="flock-a",
        persona="p",
        capabilities=[],
        permission_mode="read_only",
        version="1",
    )
    discovery = EventBusDiscoveryAdapter(identity, bus, bus, manage_transport_lifecycle=False)

    assert mesh._event_prefix == "mesh"
    assert mesh._rpc_request_prefix == "mesh.rpc"
    assert mesh._rpc_reply_prefix == "mesh.rpc.reply"
    assert discovery._announce_event_type == "mesh.realm_flock_a.announce"
    assert amqp_discovery._ANNOUNCE_ROUTING_KEY == MESH_ANNOUNCE == "mesh.announce"
    assert amqp_discovery._EXCHANGE_NAME == MeshSleipnirConfig().exchange == "mesh"


def test_the_relay_ignores_mesh_traffic_by_the_new_prefix() -> None:
    assert observation_relay._IGNORED_EVENT_PREFIXES == ("mesh.",)


def test_mesh_is_a_registered_sleipnir_namespace_with_a_registry_entry() -> None:
    assert "mesh" in EVENT_NAMESPACES
    assert MESH_ANNOUNCE in known_event_types()
    validate_event_type("mesh.realm_flock_a.announce")


def test_no_source_file_still_uses_the_old_name() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if OLD in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


async def test_an_announcement_on_the_old_name_is_not_seen() -> None:
    # No alias: a peer still on the old name and one on the new do not discover each other.
    bus = InProcessBus()
    identity = MeshIdentity(
        peer_id="new",
        realm_id="flock-a",
        persona="p",
        capabilities=[],
        permission_mode="read_only",
        version="1",
    )
    adapter = EventBusDiscoveryAdapter(identity, bus, bus, manage_transport_lifecycle=False)
    await adapter.start()
    await bus.publish(
        SleipnirEvent(
            event_type=f"{OLD}.realm_flock_a.announce",
            source="ravn:old",
            payload={
                "identity": {"peer_id": "old", "realm_id": "flock-a"},
                "action": "join",
                "status": "idle",
            },
            summary="old peer joins",
            domain="code",
            urgency=0.3,
            timestamp=datetime.now(UTC),
        )
    )
    await bus.flush()

    assert adapter.peers() == {}
    await adapter.stop()
