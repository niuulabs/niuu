"""Tests for the resident directory (ravn.api.residents).

Ravens are discovery-only (standalone residents deployed via the skuld
chart); sessions still proxy the Forge API for ravn_flock rooms and merge
in the discovered residents.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import httpx
import pytest
import respx

from niuu.domain.models import Principal
from ravn.adapters.platform_runtime import HttpPlatformRuntimeAdapter
from ravn.api.residents import ResidentDirectory, forward_auth
from ravn.ports.resident_discovery import StandaloneResident

_BASE = "http://volundr.test"
_PRINCIPAL = Principal(
    user_id="user-a",
    email="user-a@example.test",
    tenant_id="tenant-a",
    roles=["volundr:developer"],
)


def _directory(
    *,
    discovery=None,
    managed: list[dict] | None = None,
) -> ResidentDirectory:
    managed_runtimes = managed or []

    def resident_response(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/resident-runtimes"):
            return httpx.Response(200, json=managed_runtimes)
        runtime_id = request.url.path.rsplit("/", 1)[-1]
        runtime = next(
            (item for item in managed_runtimes if str(item.get("id")) == runtime_id),
            None,
        )
        return httpx.Response(200, json=runtime) if runtime is not None else httpx.Response(404)

    respx.route(
        method="GET",
        url__regex=re.compile(
            rf"{re.escape(_BASE)}/api/v1/forge/resident-runtimes(?:/[^/?]+)?(?:\?.*)?$"
        ),
    ).mock(side_effect=resident_response)
    return ResidentDirectory(
        platform=HttpPlatformRuntimeAdapter(base_url=_BASE),
        discovery=discovery,
    )


def _standalone(**overrides) -> StandaloneResident:
    fields = {
        "id": "resident-muninn",
        "resident_name": "Muninn Standalone",
        "persona_name": "product-steward",
        "status": "active",
        "model": "claude-fable-5",
        "chat_endpoint": "ws://resident-muninn/session",
        "location": "volundr/resident-muninn",
        "created_at": "2026-07-01T09:00:00Z",
    }
    fields.update(overrides)
    return StandaloneResident(**fields)


class _StaticDiscovery:
    def __init__(self, residents: list[StandaloneResident]) -> None:
        self._residents = residents

    async def list_residents(self) -> list[StandaloneResident]:
        return self._residents


class _FailingDiscovery:
    async def list_residents(self) -> list[StandaloneResident]:
        raise RuntimeError("cluster unreachable")


def _forge_session(**overrides) -> dict:
    session = {
        "id": "11111111-2222-4333-8444-555555555555",
        "name": "research campaign",
        "model": "claude-opus-4-8",
        "status": "running",
        "chat_endpoint": "ws://host:8080/s/1111/session",
        "created_at": "2026-07-04T10:00:00Z",
        "updated_at": "2026-07-04T11:00:00Z",
        "pod_name": "local-1111",
        "workload_type": "ravn_flock",
    }
    session.update(overrides)
    return session


def _managed_runtime(**overrides) -> dict:
    runtime = {
        "id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "name": "Muninn Managed",
        "personaName": "product-steward",
        "model": "gpt-5.6",
        "backend": "openshell",
        "engine": "ravn",
        "profileId": "ravn-openshell",
        "desiredState": "running",
        "observedState": "active",
        "backendRef": {"kind": "Sandbox", "name": "muninn"},
        "endpoints": [{"kind": "chat", "protocol": "skuld-v1", "url": "/s/muninn"}],
        "capabilities": ["chat", "logs"],
        "conditions": [],
        "createdAt": "2026-07-10T10:00:00Z",
        "updatedAt": "2026-07-10T11:00:00Z",
    }
    runtime.update(overrides)
    return runtime


class TestForwardAuth:
    def test_forwards_identity_headers_and_dev_params(self):
        request = SimpleNamespace(
            headers={"authorization": "Bearer tok", "x-auth-user-id": "alice", "other": "x"},
            query_params={"devUserId": "alice", "unrelated": "y"},
        )
        headers, params = forward_auth(request)
        assert headers == {"authorization": "Bearer tok", "x-auth-user-id": "alice"}
        assert params == {"devUserId": "alice"}

    def test_empty_request(self):
        request = SimpleNamespace(headers={}, query_params={})
        headers, params = forward_auth(request)
        assert headers == {}
        assert params == {}


class TestListRavens:
    @respx.mock
    async def test_lists_discovered_residents(self):
        directory = _directory(
            discovery=_StaticDiscovery([_standalone()]),
        )
        ravens = await directory.list_ravens(_PRINCIPAL, {}, {})
        assert len(ravens) == 1
        raven = ravens[0]
        assert raven["kind"] == "resident"
        assert raven["persona_name"] == "product-steward"
        assert raven["resident_name"] == "Muninn Standalone"
        assert raven["peer_id"] == ""
        assert raven["chat_endpoint"] == "ws://resident-muninn/session"
        assert raven["status"] == "active"
        assert raven["session_id"] == raven["id"] == "resident-muninn"
        assert raven["location"] == "volundr/resident-muninn"
        assert raven["deployment"] == "standalone"

    @respx.mock
    async def test_no_discovery_configured_returns_empty(self):
        directory = _directory()
        assert await directory.list_ravens(_PRINCIPAL, {}, {}) == []

    @respx.mock
    async def test_discovery_failure_is_not_reported_as_an_empty_fleet(self):
        directory = _directory(discovery=_FailingDiscovery())
        with pytest.raises(RuntimeError, match="cluster unreachable"):
            await directory.list_ravens(_PRINCIPAL, {}, {})

    @respx.mock
    async def test_managed_resident_is_authoritative_over_discovery(self):
        managed = _managed_runtime()
        directory = _directory(
            managed=[managed],
            discovery=_StaticDiscovery([_standalone(id=managed["id"])]),
        )

        ravens = await directory.list_ravens(_PRINCIPAL, {}, {})

        assert len(ravens) == 1
        assert ravens[0]["managed"] is True
        assert ravens[0]["backend"] == "openshell"
        assert ravens[0]["profile_id"] == "ravn-openshell"
        assert ravens[0]["chat_endpoint"] == "/s/muninn"
        assert ravens[0]["status"] == "active"

    @respx.mock
    async def test_discovery_visibility_is_owner_and_tenant_scoped(self):
        directory = _directory(
            discovery=_StaticDiscovery(
                [
                    _standalone(id="system"),
                    _standalone(
                        id="mine",
                        visibility="user",
                        owner_id="user-a",
                        tenant_id="tenant-a",
                    ),
                    _standalone(
                        id="theirs",
                        visibility="user",
                        owner_id="user-b",
                        tenant_id="tenant-a",
                    ),
                    _standalone(
                        id="tenant",
                        visibility="tenant",
                        tenant_id="tenant-a",
                    ),
                ]
            )
        )

        ravens = await directory.list_ravens(_PRINCIPAL, {}, {})

        assert {raven["id"] for raven in ravens} == {"system", "mine", "tenant"}

    @respx.mock
    async def test_user_resident_admin_visibility_requires_matching_tenant(self):
        directory = _directory(
            discovery=_StaticDiscovery(
                [
                    _standalone(
                        id="tenantless",
                        visibility="user",
                        owner_id="user-a",
                        tenant_id="",
                    ),
                    _standalone(
                        id="tenant-b",
                        visibility="user",
                        owner_id="user-b",
                        tenant_id="tenant-b",
                    ),
                ]
            )
        )
        other_tenant_admin = Principal(
            user_id="admin-b",
            email="admin-b@example.test",
            tenant_id="tenant-b",
            roles=["volundr:admin"],
        )

        owner_ravens = await directory.list_ravens(_PRINCIPAL, {}, {})
        admin_ravens = await directory.list_ravens(other_tenant_admin, {}, {})

        assert {raven["id"] for raven in owner_ravens} == {"tenantless"}
        assert {raven["id"] for raven in admin_ravens} == {"tenant-b"}

    @respx.mock
    async def test_lists_target_profiles_without_backend_configuration(self):
        route = respx.get(f"{_BASE}/api/v1/forge/resident-profiles").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "ravn-openshell",
                        "displayName": "Ravn on OpenShell",
                        "backend": "openshell",
                        "engine": "ravn",
                    }
                ],
            )
        )
        directory = _directory()

        profiles = await directory.list_profiles(
            {"x-auth-user-id": "user-a"},
            {"devUserId": "user-a"},
        )

        assert profiles[0]["id"] == "ravn-openshell"
        assert route.calls.last.request.headers["x-auth-user-id"] == "user-a"


class TestManagedResidentCommands:
    @respx.mock
    async def test_create_and_lifecycle_use_target_platform_adapter(self):
        runtime = _managed_runtime()
        create_route = respx.post(f"{_BASE}/api/v1/forge/resident-runtimes").mock(
            return_value=httpx.Response(201, json=runtime)
        )
        restart_route = respx.post(
            f"{_BASE}/api/v1/forge/resident-runtimes/{runtime['id']}/restart"
        ).mock(return_value=httpx.Response(200, json=runtime))
        directory = _directory()

        created = await directory.create_raven(
            {"name": "Muninn", "profile_id": "ravn-helm"},
            {"x-auth-user-id": "user-a"},
            {},
        )
        restarted = await directory.control_raven(runtime["id"], "restart", {}, {})

        assert created["managed"] is True
        assert created["backend"] == "openshell"
        assert restarted["id"] == runtime["id"]
        assert create_route.called
        assert restart_route.called

    @respx.mock
    async def test_delete_uses_target_platform_adapter(self):
        runtime_id = _managed_runtime()["id"]
        route = respx.delete(f"{_BASE}/api/v1/forge/resident-runtimes/{runtime_id}").mock(
            return_value=httpx.Response(204)
        )
        directory = _directory()

        await directory.delete_raven(runtime_id, {}, {})

        assert route.called

    @respx.mock
    async def test_logs_use_target_platform_adapter(self):
        runtime_id = _managed_runtime()["id"]
        route = respx.get(f"{_BASE}/api/v1/forge/resident-runtimes/{runtime_id}/logs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "entries": [{"timestampMs": 1, "message": "PROC:LAUNCH ravn"}],
                    "bufferTotal": 1,
                },
            )
        )
        directory = _directory()

        logs = await directory.get_raven_logs(
            runtime_id,
            lines=25,
            sources=("sandbox",),
            min_level="INFO",
            auth_headers={"x-auth-user-id": "user-a"},
            auth_params={},
        )

        assert logs["bufferTotal"] == 1
        assert route.calls.last.request.url.params.get("source") == "sandbox"


class TestStandaloneMerge:
    @respx.mock
    async def test_list_sessions_appends_standalone_residents(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[_forge_session()])
        )
        directory = _directory(
            discovery=_StaticDiscovery([_standalone()]),
        )
        sessions = await directory.list_sessions(_PRINCIPAL, {}, {})
        assert [s["id"] for s in sessions] == [
            "11111111-2222-4333-8444-555555555555",
            "resident-muninn",
        ]

    @respx.mock
    async def test_forge_wins_on_id_collision(self):
        forge = _forge_session()
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[forge])
        )
        directory = _directory(
            discovery=_StaticDiscovery([_standalone(id=forge["id"])]),
        )
        sessions = await directory.list_sessions(_PRINCIPAL, {}, {})
        assert len(sessions) == 1
        assert sessions[0]["title"] == "research campaign"

    @respx.mock
    async def test_discovery_failure_keeps_forge_sessions(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[_forge_session()])
        )
        directory = _directory(discovery=_FailingDiscovery())
        sessions = await directory.list_sessions(_PRINCIPAL, {}, {})
        assert len(sessions) == 1

    @respx.mock
    async def test_forge_failure_still_propagates(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(return_value=httpx.Response(500))
        directory = _directory(
            discovery=_StaticDiscovery([_standalone()]),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await directory.list_sessions(_PRINCIPAL, {}, {})

    @respx.mock
    async def test_list_sessions_appends_standalone_with_status_mapping(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[_forge_session()])
        )
        directory = _directory(
            discovery=_StaticDiscovery(
                [
                    _standalone(),
                    _standalone(id="resident-idle", status="idle"),
                    _standalone(id="resident-off", status="suspended"),
                    _standalone(id="resident-bad", status="failed"),
                    _standalone(id="resident-done", status="completed"),
                ]
            ),
        )
        sessions = await directory.list_sessions(_PRINCIPAL, {}, {})
        standalone = {s["id"]: s for s in sessions[1:]}
        assert standalone["resident-muninn"]["status"] == "running"
        assert standalone["resident-idle"]["status"] == "idle"
        assert "resident-off" not in standalone
        assert "resident-bad" not in standalone
        assert "resident-done" not in standalone
        session = standalone["resident-muninn"]
        assert session["ravn_id"] == "resident-muninn"
        assert session["title"] == "Muninn Standalone"
        assert session["persona_name"] == "product-steward"
        assert session["chat_endpoint"] == "ws://resident-muninn/session"

    @respx.mock
    async def test_get_session_falls_back_to_standalone(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/resident-muninn").mock(
            return_value=httpx.Response(404)
        )
        directory = _directory(
            discovery=_StaticDiscovery([_standalone()]),
        )
        session = await directory.get_session("resident-muninn", _PRINCIPAL, {}, {})
        assert session is not None
        assert session["status"] == "running"
        assert session["title"] == "Muninn Standalone"

    @respx.mock
    async def test_get_session_stopped_standalone_returns_none(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/resident-off").mock(
            return_value=httpx.Response(404)
        )
        directory = _directory(
            discovery=_StaticDiscovery([_standalone(id="resident-off", status="suspended")]),
        )
        assert await directory.get_session("resident-off", _PRINCIPAL, {}, {}) is None


class TestListSessions:
    @respx.mock
    async def test_lists_only_flock_sessions_with_chat(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _forge_session(),
                    _forge_session(id="plain", workload_type="session"),
                ],
            )
        )
        directory = _directory()
        sessions = await directory.list_sessions(_PRINCIPAL, {}, {})
        # flock only, NOT the plain coding session
        assert len(sessions) == 1
        flock = sessions[0]
        assert flock["ravn_id"] == "11111111-2222-4333-8444-555555555555"
        assert flock["persona_name"] == "research campaign"
        assert flock["title"] == "research campaign"
        assert flock["status"] == "running"
        assert flock["chat_endpoint"] == "ws://host:8080/s/1111/session"

    @respx.mock
    async def test_forwards_auth(self):
        route = respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[])
        )
        directory = _directory()
        await directory.list_sessions(
            _PRINCIPAL,
            {"x-auth-user-id": "alice"},
            {"devUserId": "alice"},
        )
        request = route.calls.last.request
        assert request.headers["x-auth-user-id"] == "alice"
        assert "devUserId=alice" in str(request.url)

    @respx.mock
    async def test_status_mapping(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _forge_session(id="a", status="stopped"),
                    _forge_session(id="b", status="failed"),
                    _forge_session(id="c", status="starting"),
                ],
            )
        )
        directory = _directory()
        statuses = [s["status"] for s in await directory.list_sessions(_PRINCIPAL, {}, {})]
        assert statuses == ["idle"]

    @respx.mock
    async def test_get_session_non_ravn_returns_none(self):
        session = _forge_session(workload_type="session")
        respx.get(f"{_BASE}/api/v1/forge/sessions/{session['id']}").mock(
            return_value=httpx.Response(200, json=session)
        )
        directory = _directory()
        assert await directory.get_session(session["id"], _PRINCIPAL, {}, {}) is None

    @respx.mock
    async def test_get_session_stopped_flock_returns_none(self):
        session = _forge_session(status="stopped")
        respx.get(f"{_BASE}/api/v1/forge/sessions/{session['id']}").mock(
            return_value=httpx.Response(200, json=session)
        )
        directory = _directory()
        assert await directory.get_session(session["id"], _PRINCIPAL, {}, {}) is None

    @respx.mock
    async def test_list_sessions_unexpected_payload_fails_loudly(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json={"not": "a list"})
        )
        directory = _directory()
        with pytest.raises(RuntimeError, match="unexpected payload"):
            await directory.list_sessions(_PRINCIPAL, {}, {})

    @respx.mock
    async def test_get_session_rejects_malformed_id(self):
        directory = _directory()
        assert await directory.get_session("../escape", _PRINCIPAL, {}, {}) is None

    @respx.mock
    async def test_get_session_server_error_propagates(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/boom").mock(return_value=httpx.Response(500))
        directory = _directory()
        with pytest.raises(httpx.HTTPStatusError):
            await directory.get_session("boom", _PRINCIPAL, {}, {})

    @respx.mock
    async def test_aclose_closes_pooled_client(self):
        directory = _directory()
        await directory.aclose()
        assert directory._platform._client.is_closed

    @respx.mock
    async def test_get_session_flock(self):
        session = _forge_session()
        respx.get(f"{_BASE}/api/v1/forge/sessions/{session['id']}").mock(
            return_value=httpx.Response(200, json=session)
        )
        directory = _directory()
        got = await directory.get_session(session["id"], _PRINCIPAL, {}, {})
        assert got is not None
        assert got["chat_endpoint"] == "ws://host:8080/s/1111/session"


class TestGetRaven:
    @respx.mock
    async def test_get_discovered_resident(self):
        directory = _directory(
            discovery=_StaticDiscovery([_standalone()]),
        )
        raven = await directory.get_raven("resident-muninn", _PRINCIPAL, {}, {})
        assert raven is not None
        assert raven["deployment"] == "standalone"
        assert raven["resident_name"] == "Muninn Standalone"

    @respx.mock
    async def test_get_unknown_id_returns_none(self):
        directory = _directory(
            discovery=_StaticDiscovery([_standalone()]),
        )
        assert await directory.get_raven("unknown", _PRINCIPAL, {}, {}) is None

    @respx.mock
    async def test_no_discovery_configured_returns_none(self):
        directory = _directory()
        assert await directory.get_raven("resident-muninn", _PRINCIPAL, {}, {}) is None

    @respx.mock
    async def test_rejects_malformed_managed_id_before_platform_call(self):
        directory = _directory()

        assert await directory.get_raven("../escape", _PRINCIPAL, {}, {}) is None

    @respx.mock
    async def test_discovery_failure_is_not_reported_as_a_missing_resident(self):
        directory = _directory(discovery=_FailingDiscovery())
        with pytest.raises(RuntimeError, match="cluster unreachable"):
            await directory.get_raven("resident-muninn", _PRINCIPAL, {}, {})
