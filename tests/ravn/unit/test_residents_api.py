"""Tests for the resident discovery directory (ravn.api.residents)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from ravn.api.residents import ResidentDirectory, forward_auth

_BASE = "http://volundr.test"


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


class TestGetRaven:
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
