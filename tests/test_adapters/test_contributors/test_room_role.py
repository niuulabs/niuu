"""Tests for RoomRoleSourceContributor."""

from volundr.adapters.outbound.contributors.room_role import RoomRoleSourceContributor
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext


def _session() -> Session:
    return Session(name="session", model="gpt-5.5", source=GitSource())


async def test_deployment_default_contributes_nothing_on_kubernetes():
    """Existing clusters must render byte-identical values until they opt in."""
    contributor = RoomRoleSourceContributor(room_role_source="deployment")

    contribution = await contributor.contribute(
        _session(), SessionContext(runtime_backend="kubernetes")
    )

    assert contribution.values == {}


async def test_remote_on_kubernetes_renders_wsauth_room_role_source():
    contributor = RoomRoleSourceContributor(room_role_source="remote")

    contribution = await contributor.contribute(
        _session(), SessionContext(runtime_backend="kubernetes")
    )

    assert contribution.values == {
        "wsAuth": {
            "room_role_source": "remote",
            "room_role_remote": {
                "adapter": "skuld.room_role_remote.RemoteAuthorizationAdapter",
                "kwargs": {
                    "scope": "forge:session:room-role",
                    "cache_ttl_seconds": 5.0,
                },
            },
        }
    }


async def test_remote_on_process_backend_contributes_nothing():
    """process already honors grants via the session proxy — this contributor
    is only for the Gateway-routed backends."""
    contributor = RoomRoleSourceContributor(room_role_source="remote")

    contribution = await contributor.contribute(
        _session(), SessionContext(runtime_backend="process")
    )

    assert contribution.values == {}


async def test_remote_on_openshell_and_vm_also_renders_the_contribution():
    """Defensive inclusion, matching REMOTE_CAPABLE_RUNTIME_BACKENDS — not yet
    independently verified end-to-end for these backends."""
    contributor = RoomRoleSourceContributor(room_role_source="remote")

    for backend in ("openshell", "vm"):
        contribution = await contributor.contribute(
            _session(), SessionContext(runtime_backend=backend)
        )
        assert contribution.values["wsAuth"]["room_role_source"] == "remote"


async def test_custom_adapter_scope_and_ttl_are_forwarded():
    contributor = RoomRoleSourceContributor(
        room_role_source="remote",
        adapter="custom.Adapter",
        scope="custom:scope",
        cache_ttl_seconds=30.0,
    )

    contribution = await contributor.contribute(
        _session(), SessionContext(runtime_backend="kubernetes")
    )

    remote = contribution.values["wsAuth"]["room_role_remote"]
    assert remote["adapter"] == "custom.Adapter"
    assert remote["kwargs"] == {"scope": "custom:scope", "cache_ttl_seconds": 30.0}


def test_contributor_name_is_room_role_source():
    assert RoomRoleSourceContributor().name == "room_role_source"
