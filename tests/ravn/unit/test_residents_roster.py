"""ResidentDirectory merges the configured roster into the fleet read paths.

Covers ``list_ravens``, ``get_raven``, ``list_sessions``, ``get_session`` and
the per-resident sessions listing, plus the managed > discovered > rostered
id precedence and the roster's visibility rules.
"""

from __future__ import annotations

import re
from dataclasses import replace

import httpx
import pytest
import respx

from niuu.domain.models import InstanceVisibility, Principal
from ravn.adapters.platform_runtime import HttpPlatformRuntimeAdapter
from ravn.api.residents import ResidentDirectory
from ravn.ports.resident_discovery import StandaloneResident

_BASE = "http://volundr.test"
_ROOM = "wss://yggdrasil.niuu.world/rooms/runa/ws/ravn"
_PRINCIPAL = Principal(
    user_id="user-a",
    email="user-a@example.test",
    tenant_id="tenant-a",
    roles=["volundr:developer"],
)


class _Static:
    def __init__(self, residents: list[StandaloneResident]) -> None:
        self._residents = residents

    async def list_residents(self) -> list[StandaloneResident]:
        return self._residents


class _Failing:
    async def list_residents(self) -> list[StandaloneResident]:
        raise RuntimeError("roster unreachable")


def _directory(*, managed: list[dict] | None = None, discovery=None, roster=None):
    runtimes = managed or []

    def resident_response(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/resident-runtimes"):
            return httpx.Response(200, json=runtimes)
        runtime_id = request.url.path.rsplit("/", 1)[-1]
        runtime = next((item for item in runtimes if item["id"] == runtime_id), None)
        return httpx.Response(200, json=runtime) if runtime else httpx.Response(404)

    respx.route(
        method="GET",
        url__regex=re.compile(
            rf"{re.escape(_BASE)}/api/v1/forge/resident-runtimes(?:/[^/?]+)?(?:\?.*)?$"
        ),
    ).mock(side_effect=resident_response)
    return ResidentDirectory(
        platform=HttpPlatformRuntimeAdapter(base_url=_BASE),
        discovery=discovery,
        roster=roster,
        platform_tenant_id="tenant-a",
    )


def _resident(**overrides) -> StandaloneResident:
    fields = {
        "id": "valkyrie-runa",
        "resident_name": "Runa",
        "persona_name": "k8s-valkyrie",
        "status": "active",
        "chat_endpoint": _ROOM,
        "location": "Runa cluster",
        "updated_at": "2026-10-28T11:59:00+00:00",
        "deployment": "valkyrie",
        "conditions": [{"type": "Observed", "status": "true", "reason": "Fresh"}],
    }
    fields.update(overrides)
    return StandaloneResident(**fields)


def _discovered(**overrides) -> StandaloneResident:
    fields = {
        "id": "resident-muninn",
        "resident_name": "Muninn",
        "persona_name": "product-steward",
        "status": "active",
        "chat_endpoint": "ws://resident-muninn/session",
    }
    fields.update(overrides)
    return StandaloneResident(**fields)


def _managed(**overrides) -> dict:
    runtime = {
        "id": "shared-managed",
        "name": "From Forge",
        "personaName": "product-steward",
        "backend": "openshell",
        "engine": "ravn",
        "desiredState": "running",
        "observedState": "active",
        "endpoints": [{"kind": "chat", "protocol": "skuld-v1", "url": "/s/muninn"}],
        "capabilities": [],
        "conditions": [],
    }
    runtime.update(overrides)
    return runtime


def _forge_session(**overrides) -> dict:
    session = {
        "id": "11111111-2222-4333-8444-555555555555",
        "name": "research campaign",
        "status": "running",
        "chat_endpoint": "ws://host:8080/s/1111/session",
        "workload_type": "ravn_flock",
    }
    session.update(overrides)
    return session


def _forge_sessions(*sessions: dict) -> None:
    respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
        return_value=httpx.Response(200, json=list(sessions))
    )


class TestListRavens:
    @respx.mock
    async def test_roster_resident_uses_the_standalone_raven_shape(self):
        directory = _directory(roster=_Static([_resident()]))

        (raven,) = await directory.list_ravens(_PRINCIPAL, {}, {})

        assert raven["id"] == "valkyrie-runa"
        assert raven["resident_name"] == "Runa"
        assert raven["persona_name"] == "k8s-valkyrie"
        assert raven["kind"] == "resident"
        assert raven["deployment"] == "valkyrie"
        assert raven["location"] == "Runa cluster"
        assert raven["status"] == "active"
        assert raven["observed_state"] == "active"
        assert raven["desired_state"] == "running"
        assert raven["chat_endpoint"] == _ROOM
        assert raven["capabilities"] == ["chat"]
        assert raven["updated_at"] == "2026-10-28T11:59:00+00:00"
        assert raven["conditions"][0]["reason"] == "Fresh"
        assert raven["managed"] is False

    @respx.mock
    async def test_resident_without_a_room_is_listed_without_chat(self):
        roster = _Static([_resident(chat_endpoint=None, capabilities=[], status="idle")])
        directory = _directory(roster=roster)

        (raven,) = await directory.list_ravens(_PRINCIPAL, {}, {})

        assert raven["chat_endpoint"] is None
        assert raven["capabilities"] == []
        assert raven["status"] == "idle"

    @respx.mock
    async def test_discovered_standalone_residents_still_advertise_chat(self):
        directory = _directory(discovery=_Static([_discovered()]))

        (raven,) = await directory.list_ravens(_PRINCIPAL, {}, {})

        assert raven["capabilities"] == ["chat"]
        assert raven["deployment"] == "standalone"

    @respx.mock
    async def test_managed_then_discovered_then_rostered_precedence(self):
        directory = _directory(
            managed=[_managed()],
            discovery=_Static([_discovered(id="shared-discovered")]),
            roster=_Static(
                [
                    _resident(id="shared-managed"),
                    _resident(id="shared-discovered"),
                    _resident(id="valkyrie-sigrun", resident_name="Sigrun"),
                ]
            ),
        )

        ravens = await directory.list_ravens(_PRINCIPAL, {}, {})

        by_id = {raven["id"]: raven for raven in ravens}
        assert list(by_id) == ["shared-managed", "shared-discovered", "valkyrie-sigrun"]
        assert by_id["shared-managed"]["managed"] is True
        assert by_id["shared-discovered"]["deployment"] == "standalone"
        assert by_id["valkyrie-sigrun"]["deployment"] == "valkyrie"

    @respx.mock
    async def test_discovered_id_the_caller_cannot_see_still_shadows_the_roster(self):
        hidden = _discovered(
            id="valkyrie-runa",
            visibility=InstanceVisibility.USER,
            owner_id="someone-else",
        )
        directory = _directory(discovery=_Static([hidden]), roster=_Static([_resident()]))

        assert await directory.list_ravens(_PRINCIPAL, {}, {}) == []

    @respx.mock
    async def test_roster_visibility_follows_the_discovered_rules(self):
        roster = _Static(
            [
                _resident(id="v-system"),
                _resident(id="v-mine", visibility=InstanceVisibility.USER, owner_id="user-a"),
                _resident(id="v-theirs", visibility=InstanceVisibility.USER, owner_id="user-b"),
                _resident(
                    id="v-tenant", visibility=InstanceVisibility.TENANT, tenant_id="tenant-a"
                ),
                _resident(
                    id="v-other-tenant",
                    visibility=InstanceVisibility.TENANT,
                    tenant_id="tenant-b",
                ),
            ]
        )
        directory = _directory(roster=roster)

        ravens = await directory.list_ravens(_PRINCIPAL, {}, {})

        assert [raven["id"] for raven in ravens] == ["v-system", "v-mine", "v-tenant"]

    @respx.mock
    async def test_roster_failure_is_not_reported_as_an_empty_fleet(self):
        directory = _directory(roster=_Failing())

        with pytest.raises(RuntimeError, match="roster unreachable"):
            await directory.list_ravens(_PRINCIPAL, {}, {})

    @respx.mock
    async def test_no_roster_configured_changes_nothing(self):
        assert await _directory().list_ravens(_PRINCIPAL, {}, {}) == []


class TestGetRaven:
    @respx.mock
    async def test_finds_a_rostered_resident(self):
        directory = _directory(roster=_Static([_resident()]))

        raven = await directory.get_raven("valkyrie-runa", _PRINCIPAL, {}, {})

        assert raven is not None
        assert raven["resident_name"] == "Runa"
        assert raven["deployment"] == "valkyrie"

    @respx.mock
    async def test_hides_a_roster_resident_from_other_users(self):
        roster = _Static([_resident(visibility=InstanceVisibility.USER, owner_id="user-b")])
        directory = _directory(roster=roster)

        assert await directory.get_raven("valkyrie-runa", _PRINCIPAL, {}, {}) is None

    @respx.mock
    async def test_unknown_id_is_none(self):
        directory = _directory(roster=_Static([_resident()]))

        assert await directory.get_raven("valkyrie-nobody", _PRINCIPAL, {}, {}) is None

    @respx.mock
    async def test_prefers_discovered_over_rostered(self):
        directory = _directory(
            discovery=_Static([_discovered(id="valkyrie-runa")]),
            roster=_Static([_resident()]),
        )

        raven = await directory.get_raven("valkyrie-runa", _PRINCIPAL, {}, {})

        assert raven is not None
        assert raven["deployment"] == "standalone"

    @respx.mock
    async def test_roster_failure_is_not_reported_as_a_missing_resident(self):
        directory = _directory(roster=_Failing())

        with pytest.raises(RuntimeError, match="roster unreachable"):
            await directory.get_raven("valkyrie-runa", _PRINCIPAL, {}, {})


class TestListSessions:
    @respx.mock
    async def test_yields_the_room_channel(self):
        _forge_sessions(_forge_session())
        directory = _directory(roster=_Static([_resident()]))

        result = await directory.list_sessions(_PRINCIPAL, {}, {})

        session = result.sessions[-1]
        assert session["id"] == session["ravn_id"] == "valkyrie-runa"
        assert session["title"] == "Runa"
        assert session["status"] == "running"
        assert session["chat_endpoint"] == _ROOM
        assert session["capabilities"] == ["chat"]
        assert result.source_failures == []

    @respx.mock
    async def test_skips_a_resident_with_no_room(self):
        _forge_sessions()
        directory = _directory(roster=_Static([_resident(chat_endpoint=None, capabilities=[])]))

        result = await directory.list_sessions(_PRINCIPAL, {}, {})

        assert result.sessions == []

    @respx.mock
    async def test_idle_roster_resident_is_still_a_live_session(self):
        _forge_sessions()
        directory = _directory(roster=_Static([_resident(status="idle")]))

        result = await directory.list_sessions(_PRINCIPAL, {}, {})

        assert [session["status"] for session in result.sessions] == ["idle"]

    @respx.mock
    async def test_dedupes_against_every_other_source(self):
        forge = _forge_session()
        _forge_sessions(forge)
        directory = _directory(
            managed=[_managed()],
            discovery=_Static([_discovered(id="shared-discovered")]),
            roster=_Static(
                [
                    _resident(id=forge["id"]),
                    _resident(id="shared-managed"),
                    _resident(id="shared-discovered"),
                ]
            ),
        )

        result = await directory.list_sessions(_PRINCIPAL, {}, {})

        ids = [session["id"] for session in result.sessions]
        assert sorted(ids) == sorted([forge["id"], "shared-managed", "shared-discovered"])

    @respx.mock
    async def test_reports_a_failed_roster_and_keeps_the_other_sessions(self):
        _forge_sessions(_forge_session())
        directory = _directory(roster=_Failing())

        result = await directory.list_sessions(_PRINCIPAL, {}, {})

        assert len(result.sessions) == 1
        (failure,) = result.source_failures
        assert failure["instanceId"] == "resident-roster"
        assert "roster unreachable" in failure["error"]

    @respx.mock
    async def test_hides_invisible_roster_residents(self):
        _forge_sessions()
        roster = _Static([_resident(visibility=InstanceVisibility.USER, owner_id="user-b")])
        directory = _directory(roster=roster)

        result = await directory.list_sessions(_PRINCIPAL, {}, {})

        assert result.sessions == []


class TestGetSession:
    @respx.mock
    async def test_returns_the_roster_resident(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/valkyrie-runa").mock(
            return_value=httpx.Response(404)
        )
        directory = _directory(roster=_Static([_resident()]))

        session = await directory.get_session("valkyrie-runa", _PRINCIPAL, {}, {})

        assert session is not None
        assert session["chat_endpoint"] == _ROOM

    @respx.mock
    async def test_without_a_room_or_visibility_or_listing_is_none(self):
        respx.get(re.compile(rf"{re.escape(_BASE)}/api/v1/forge/sessions/.*")).mock(
            return_value=httpx.Response(404)
        )
        roster = _Static(
            [
                _resident(id="no-room", chat_endpoint=None, capabilities=[]),
                _resident(id="not-mine", visibility=InstanceVisibility.USER, owner_id="user-b"),
            ]
        )
        directory = _directory(roster=roster)

        assert await directory.get_session("no-room", _PRINCIPAL, {}, {}) is None
        assert await directory.get_session("not-mine", _PRINCIPAL, {}, {}) is None
        assert await directory.get_session("unlisted", _PRINCIPAL, {}, {}) is None


class TestResidentSessionsListing:
    @respx.mock
    async def test_yields_the_room(self):
        directory = _directory(roster=_Static([_resident()]))

        sessions = await directory.list_resident_sessions("valkyrie-runa", _PRINCIPAL, {}, {})

        assert [session["chat_endpoint"] for session in sessions] == [_ROOM]
        assert sessions[0]["ravn_id"] == "valkyrie-runa"

    @respx.mock
    async def test_is_empty_without_a_room(self):
        directory = _directory(roster=_Static([_resident(chat_endpoint=None, capabilities=[])]))

        assert await directory.list_resident_sessions("valkyrie-runa", _PRINCIPAL, {}, {}) == []

    @respx.mock
    async def test_prefers_a_managed_runtime_of_the_same_id(self):
        # Managed runtime ids are platform UUIDs, so only a catalog entry that
        # names a UUID can collide with one.
        shared_id = "5f0c6a8e-1b2d-4c3e-8f40-516273849abc"
        native = respx.get(f"{_BASE}/api/v1/forge/resident-runtimes/{shared_id}/sessions").mock(
            return_value=httpx.Response(200, json=[{"id": "native-1"}])
        )
        directory = _directory(
            managed=[_managed(id=shared_id)],
            roster=_Static([_resident(id=shared_id)]),
        )

        sessions = await directory.list_resident_sessions(shared_id, _PRINCIPAL, {}, {})

        assert native.called
        assert [session["id"] for session in sessions] == ["native-1"]


@pytest.mark.asyncio
@respx.mock
async def test_the_system_list_keeps_only_residents_every_member_may_see() -> None:
    """A shared view (the Observatory map) must not carry any user's own residents."""
    directory = _directory(
        managed=[{"id": "owned-runtime", "name": "Mine"}],
        discovery=_Static(
            [
                _resident(id="muninn", resident_name="Muninn"),
                _resident(
                    id="private",
                    resident_name="Private",
                    visibility=InstanceVisibility.USER,
                    owner_id="user-a",
                ),
            ]
        ),
        roster=_Static(
            [
                _resident(),
                _resident(id="muninn", resident_name="Muninn (catalog)"),
                _resident(
                    id="tenant-only",
                    visibility=InstanceVisibility.TENANT,
                    tenant_id="tenant-a",
                ),
            ]
        ),
    )

    ravens = await directory.list_system_ravens(_PRINCIPAL)

    assert [(r["id"], r["resident_name"]) for r in ravens] == [
        ("muninn", "Muninn"),
        ("valkyrie-runa", "Runa"),
    ]
    # Never asks the platform: user-owned runtimes are not part of a shared view.
    assert not respx.calls


@pytest.mark.asyncio
@respx.mock
async def test_a_system_resident_that_names_an_org_stays_inside_it():
    directory = _directory(
        managed=[],
        roster=_Static(
            [
                _resident(id="ours", resident_name="Ours", tenant_id="tenant-a"),
                _resident(id="theirs", resident_name="Theirs", tenant_id="tenant-b"),
                _resident(id="unowned", resident_name="Unowned"),
            ]
        ),
    )

    ravens = await directory.list_ravens(_PRINCIPAL, {}, {})
    shared = await directory.list_system_ravens(_PRINCIPAL)

    assert {r["id"] for r in ravens} == {"ours", "unowned"}
    assert {r["id"] for r in shared} == {"ours", "unowned"}
    assert await directory.get_raven("theirs", _PRINCIPAL, {}, {}) is None


@pytest.mark.asyncio
@respx.mock
async def test_a_resident_naming_no_org_belongs_to_the_platform_org():
    directory = _directory(
        managed=[],
        roster=_Static([_resident(id="ours"), _resident(id="theirs", tenant_id="tenant-b")]),
    )
    other_org = replace(_PRINCIPAL, tenant_id="tenant-b")

    shared = await directory.list_system_ravens(other_org)

    assert [r["id"] for r in shared] == ["theirs"]
    assert shared[0]["tenant_id"] == "tenant-b"


def test_a_tenant_resident_with_no_org_is_visible_to_nobody():
    resident = _resident(visibility=InstanceVisibility.TENANT)

    directory = _directory(managed=[])

    assert not directory._is_discovered_visible(resident, _PRINCIPAL)
