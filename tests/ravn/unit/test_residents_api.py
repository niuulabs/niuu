"""Tests for the resident discovery directory (ravn.api.residents)."""

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
        "name": "muninn-resident",
        "model": "claude-opus-4-8",
        "status": "running",
        "chat_endpoint": "ws://host:8080/s/1111/session",
        "created_at": "2026-07-04T10:00:00Z",
        "updated_at": "2026-07-04T11:00:00Z",
        "pod_name": "local-1111",
        "workload_type": "resident",
        "resident": {
            "name": "Muninn",
            "persona": "product-steward",
            "peer_id": "flock-product-steward",
        },
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
    @respx.mock
    async def test_lists_only_residents(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _forge_session(),
                    _forge_session(id="99", workload_type="session", resident=None),
                    _forge_session(id="98", workload_type="ravn_flock", resident=None),
                ],
            )
        )
        directory = ResidentDirectory(base_url=_BASE)
        ravens = await directory.list_ravens({"authorization": "Bearer t"}, {})
        assert len(ravens) == 1
        raven = ravens[0]
        assert raven["kind"] == "resident"
        assert raven["persona_name"] == "product-steward"
        assert raven["resident_name"] == "Muninn"
        assert raven["peer_id"] == "flock-product-steward"
        assert raven["chat_endpoint"] == "ws://host:8080/s/1111/session"
        assert raven["status"] == "active"
        assert raven["session_id"] == raven["id"]

    @respx.mock
    async def test_forwards_auth(self):
        route = respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[])
        )
        directory = ResidentDirectory(base_url=_BASE)
        await directory.list_ravens(
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
                    _forge_session(id="a" * 8, status="stopped"),
                    _forge_session(id="b" * 8, status="failed"),
                    _forge_session(id="c" * 8, status="starting"),
                ],
            )
        )
        directory = ResidentDirectory(base_url=_BASE)
        ravens = await directory.list_ravens({}, {})
        statuses = [r["status"] for r in ravens]
        assert statuses == ["suspended", "failed", "idle"]

    @respx.mock
    async def test_unexpected_payload_fails_loudly(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json={"not": "a list"})
        )
        directory = ResidentDirectory(base_url=_BASE)
        with pytest.raises(RuntimeError, match="unexpected payload"):
            await directory.list_ravens({}, {})

    @respx.mock
    async def test_upstream_error_propagates(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(return_value=httpx.Response(500))
        directory = ResidentDirectory(base_url=_BASE)
        with pytest.raises(httpx.HTTPStatusError):
            await directory.list_ravens({}, {})


class TestStandaloneMerge:
    @respx.mock
    async def test_list_ravens_appends_standalone_residents(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[_forge_session()])
        )
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        ravens = await directory.list_ravens({}, {})
        assert [r["id"] for r in ravens] == [
            "11111111-2222-4333-8444-555555555555",
            "resident-muninn",
        ]
        standalone = ravens[1]
        assert standalone["deployment"] == "standalone"
        assert standalone["kind"] == "resident"
        assert standalone["peer_id"] == ""
        assert standalone["session_id"] == "resident-muninn"
        assert standalone["status"] == "active"
        assert standalone["location"] == "volundr/resident-muninn"
        assert standalone["chat_endpoint"] == "ws://resident-muninn/session"

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
        ravens = await directory.list_ravens({}, {})
        assert len(ravens) == 1
        assert ravens[0]["deployment"] == "resident"
        assert ravens[0]["resident_name"] == "Muninn"

    @respx.mock
    async def test_discovery_failure_keeps_forge_results(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(200, json=[_forge_session()])
        )
        directory = ResidentDirectory(base_url=_BASE, discovery=_FailingDiscovery())
        ravens = await directory.list_ravens({}, {})
        assert len(ravens) == 1
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
            await directory.list_ravens({}, {})
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
    async def test_get_raven_falls_back_to_standalone(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/resident-muninn").mock(
            return_value=httpx.Response(404)
        )
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        raven = await directory.get_raven("resident-muninn", {}, {})
        assert raven is not None
        assert raven["deployment"] == "standalone"
        assert raven["resident_name"] == "Muninn Standalone"

    @respx.mock
    async def test_get_raven_unknown_id_still_none(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/unknown").mock(
            return_value=httpx.Response(404)
        )
        directory = ResidentDirectory(
            base_url=_BASE,
            discovery=_StaticDiscovery([_standalone()]),
        )
        assert await directory.get_raven("unknown", {}, {}) is None

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
    async def test_lists_resident_and_flock_sessions_with_chat(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _forge_session(),  # resident
                    _forge_session(
                        id="flock-1",
                        workload_type="ravn_flock",
                        resident=None,
                        name="research campaign",
                    ),
                    _forge_session(id="plain", workload_type="session", resident=None),
                ],
            )
        )
        directory = ResidentDirectory(base_url=_BASE)
        sessions = await directory.list_sessions({}, {})
        # resident + flock, NOT the plain coding session
        assert len(sessions) == 2
        resident = sessions[0]
        assert resident["ravn_id"] == "flock-product-steward"
        assert resident["persona_name"] == "product-steward"
        assert resident["status"] == "running"
        assert resident["chat_endpoint"] == "ws://host:8080/s/1111/session"
        flock = sessions[1]
        assert flock["ravn_id"] == "flock-1"  # falls back to session id
        assert flock["chat_endpoint"]

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
        session = _forge_session(workload_type="session", resident=None)
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
    async def test_get_session_resident(self):
        session = _forge_session()
        respx.get(f"{_BASE}/api/v1/forge/sessions/{session['id']}").mock(
            return_value=httpx.Response(200, json=session)
        )
        directory = ResidentDirectory(base_url=_BASE)
        got = await directory.get_session(session["id"], {}, {})
        assert got is not None
        assert got["chat_endpoint"] == "ws://host:8080/s/1111/session"


class TestGetRaven:
    async def test_rejects_malformed_id(self):
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.get_raven("../escape", {}, {}) is None

    @respx.mock
    async def test_get_resident(self):
        session = _forge_session()
        respx.get(f"{_BASE}/api/v1/forge/sessions/{session['id']}").mock(
            return_value=httpx.Response(200, json=session)
        )
        directory = ResidentDirectory(base_url=_BASE)
        raven = await directory.get_raven(session["id"], {}, {})
        assert raven is not None
        assert raven["resident_name"] == "Muninn"

    @respx.mock
    async def test_get_non_resident_returns_none(self):
        session = _forge_session(workload_type="session", resident=None)
        respx.get(f"{_BASE}/api/v1/forge/sessions/{session['id']}").mock(
            return_value=httpx.Response(200, json=session)
        )
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.get_raven(session["id"], {}, {}) is None

    @respx.mock
    async def test_get_missing_returns_none(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/nope").mock(return_value=httpx.Response(404))
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.get_raven("nope", {}, {}) is None

    @respx.mock
    async def test_get_non_uuid_returns_none(self):
        # Volundr types the path param as UUID → 422 for a junk id; must be a
        # clean None, not a 500.
        respx.get(f"{_BASE}/api/v1/forge/sessions/not-a-uuid").mock(
            return_value=httpx.Response(422)
        )
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.get_raven("not-a-uuid", {}, {}) is None

    @respx.mock
    async def test_get_forbidden_returns_none(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/other").mock(return_value=httpx.Response(403))
        directory = ResidentDirectory(base_url=_BASE)
        assert await directory.get_raven("other", {}, {}) is None

    @respx.mock
    async def test_get_server_error_propagates(self):
        respx.get(f"{_BASE}/api/v1/forge/sessions/boom").mock(return_value=httpx.Response(500))
        directory = ResidentDirectory(base_url=_BASE)
        with pytest.raises(httpx.HTTPStatusError):
            await directory.get_raven("boom", {}, {})
