"""Tests for the resident directory (ravn.api.residents).

Ravens are discovery-only (standalone residents deployed via the skuld
chart); sessions still proxy the Forge API for ravn_flock rooms and merge
in the discovered residents.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from ravn.api.residents import ResidentDirectory, forward_auth
from ravn.ports.resident_discovery import StandaloneResident

_BASE = "http://volundr.test"


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
    async def test_lists_discovered_residents(self):
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        ravens = await directory.list_ravens()
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

    async def test_no_discovery_configured_returns_empty(self):
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.list_ravens() == []

    async def test_discovery_failure_propagates(self):
        # Discovery is the only raven source; a broken discovery must not
        # masquerade as an empty fleet.
        directory = ResidentDirectory(base_url=_BASE, discovery=_FailingDiscovery())
        with pytest.raises(RuntimeError, match="cluster unreachable"):
            await directory.list_ravens()


class TestStandaloneMerge:
    @respx.mock
    async def test_list_sessions_appends_standalone_residents(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[_forge_session()])
        )
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        sessions = await directory.list_sessions({}, {})
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
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone(id=forge["id"])]),
        )
        sessions = await directory.list_sessions({}, {})
        assert len(sessions) == 1
        assert sessions[0]["title"] == "research campaign"

    @respx.mock
    async def test_discovery_failure_keeps_forge_sessions(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[_forge_session()])
        )
        directory = ResidentDirectory(base_url=_BASE, discovery=_FailingDiscovery())
        sessions = await directory.list_sessions({}, {})
        assert len(sessions) == 1

    @respx.mock
    async def test_forge_failure_still_propagates(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(return_value=httpx.Response(500))
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await directory.list_sessions({}, {})

    @respx.mock
    async def test_list_sessions_appends_standalone_with_status_mapping(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[_forge_session()])
        )
        directory = ResidentDirectory(
            base_url=_BASE,
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
        sessions = await directory.list_sessions({}, {})
        standalone = {s["id"]: s for s in sessions[1:]}
        assert standalone["resident-muninn"]["status"] == "running"
        assert standalone["resident-idle"]["status"] == "idle"
        assert standalone["resident-off"]["status"] == "stopped"
        assert standalone["resident-bad"]["status"] == "failed"
        assert standalone["resident-done"]["status"] == "stopped"
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
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        session = await directory.get_session("resident-muninn", {}, {})
        assert session is not None
        assert session["status"] == "running"
        assert session["title"] == "Muninn Standalone"


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
        directory = ResidentDirectory(base_url=_BASE)
        sessions = await directory.list_sessions({}, {})
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
        directory = ResidentDirectory(base_url=_BASE)
        await directory.list_sessions(
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
        directory = ResidentDirectory(base_url=_BASE)
        statuses = [s["status"] for s in await directory.list_sessions({}, {})]
        assert statuses == ["stopped", "failed", "idle"]

    @respx.mock
    async def test_get_session_non_ravn_returns_none(self):
        session = _forge_session(workload_type="session")
        respx.get(f"{_BASE}/api/v1/forge/sessions/{session['id']}").mock(
            return_value=httpx.Response(200, json=session)
        )
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.get_session(session["id"], {}, {}) is None

    @respx.mock
    async def test_list_sessions_unexpected_payload_fails_loudly(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json={"not": "a list"})
        )
        directory = ResidentDirectory(base_url=_BASE)
        with pytest.raises(RuntimeError, match="unexpected payload"):
            await directory.list_sessions({}, {})

    async def test_get_session_rejects_malformed_id(self):
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.get_session("../escape", {}, {}) is None

    @respx.mock
    async def test_get_session_server_error_propagates(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/boom").mock(return_value=httpx.Response(500))
        directory = ResidentDirectory(base_url=_BASE)
        with pytest.raises(httpx.HTTPStatusError):
            await directory.get_session("boom", {}, {})

    async def test_aclose_closes_pooled_client(self):
        directory = ResidentDirectory(base_url=_BASE)
        await directory.aclose()
        assert directory._client.is_closed

    @respx.mock
    async def test_get_session_flock(self):
        session = _forge_session()
        respx.get(f"{_BASE}/api/v1/forge/sessions/{session['id']}").mock(
            return_value=httpx.Response(200, json=session)
        )
        directory = ResidentDirectory(base_url=_BASE)
        got = await directory.get_session(session["id"], {}, {})
        assert got is not None
        assert got["chat_endpoint"] == "ws://host:8080/s/1111/session"


class TestGetRaven:
    async def test_get_discovered_resident(self):
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        raven = await directory.get_raven("resident-muninn")
        assert raven is not None
        assert raven["deployment"] == "standalone"
        assert raven["resident_name"] == "Muninn Standalone"

    async def test_get_unknown_id_returns_none(self):
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        assert await directory.get_raven("unknown") is None

    async def test_no_discovery_configured_returns_none(self):
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.get_raven("resident-muninn") is None

    async def test_discovery_failure_propagates(self):
        directory = ResidentDirectory(base_url=_BASE, discovery=_FailingDiscovery())
        with pytest.raises(RuntimeError, match="cluster unreachable"):
            await directory.get_raven("resident-muninn")
