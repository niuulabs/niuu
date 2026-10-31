"""The Guild facade with Forge session tokens: local node only, Volundr enforces."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import respx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import Response

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.domain.services.token_scope import FORGE_SESSION_TOKEN_USE
from tests.support.forge_session import OWNER, TENANT, bearer, build_forge_app
from tests.test_niuu.test_rest_volundr import StubInstanceService, _instance

REMOTE = "https://remote.example"
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


@pytest.fixture
async def guild():
    forge = build_forge_app()
    local = _instance(
        "local",
        base_url="embedded://local-forge",
        tenant_id=TENANT,
        is_default=True,
        config={"transport": "embedded"},
    )
    remote = _instance("remote", base_url=REMOTE, tenant_id=TENANT)
    app = FastAPI()
    # Mini-mode identity: forwarded user headers are trusted, a bare caller is dev-user.
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_volundr_router(StubInstanceService([local, remote]), embedded_forge_app=forge.app)
    )
    session = await forge.session("self")
    credential = await forge.start(session)
    return TestClient(app), forge, session, bearer(credential.token)


class TestLocalNodeOnly:
    @respx.mock
    async def test_fleet_listing_only_reads_the_local_node(self, guild) -> None:
        client, forge, session, auth = guild
        await forge.session("theirs", owner="someone-else")
        remote = respx.get(f"{REMOTE}/api/v1/forge/sessions").mock(
            return_value=Response(200, json=[])
        )
        response = client.get("/api/v1/forge/sessions", headers=auth)
        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == [str(session.id)]
        assert response.json()[0]["instance_id"] == "local"
        assert remote.called is False

    @respx.mock
    async def test_naming_a_remote_node_is_refused(self, guild) -> None:
        client, _, session, auth = guild
        remote = respx.route(host="remote.example").mock(return_value=Response(200, json={}))
        response = client.get(
            f"/api/v1/forge/sessions/{session.id}",
            params={"instance_id": "remote"},
            headers=auth,
        )
        assert response.status_code == 403
        assert "peer supervision is limited to the session's own Forge node" in response.text
        assert remote.called is False

    @respx.mock
    async def test_a_session_on_another_node_is_not_found(self, guild) -> None:
        client, _, _, auth = guild
        remote = respx.route(host="remote.example").mock(return_value=Response(200, json={}))
        response = client.get(
            "/api/v1/forge/sessions/00000000-0000-4000-8000-000000000000", headers=auth
        )
        assert response.status_code == 404
        assert remote.called is False

    async def test_the_session_stream_is_refused(self, guild) -> None:
        client, _, _, auth = guild
        response = client.get("/api/v1/forge/sessions/stream", headers=auth)
        assert response.status_code == 403

    async def test_volundr_still_enforces_through_the_facade(self, guild) -> None:
        client, forge, session, auth = guild
        peer = await forge.session("peer")
        denied = client.post(
            f"/api/v1/forge/sessions/{peer.id}/messages", json={"content": "hi"}, headers=auth
        )
        assert denied.status_code == 403
        notify = client.post(
            f"/api/v1/forge/sessions/{session.id}/notifications",
            json={"kind": "milestone", "title": "done", "idempotency_key": "k"},
            headers=auth,
        )
        assert notify.status_code == 201 and notify.json()["source"] == "agent"

    async def test_an_invalid_token_is_401_through_the_facade(self, guild) -> None:
        client, forge, _, auth = guild
        forged = auth["Authorization"][:-4] + "AAAA"
        response = client.get("/api/v1/forge/sessions", headers={"Authorization": forged})
        # the local node's refusal is the answer, not a skipped host and an empty list
        assert response.status_code == 401
        direct = forge.client.get("/api/v1/forge/sessions", headers={"Authorization": forged})
        assert direct.status_code == 401

    @respx.mock
    async def test_the_edge_refuses_routes_off_the_allow_list(self, guild) -> None:
        client, _, session, auth = guild
        remote = respx.route(host="remote.example").mock(return_value=Response(200, json={}))
        for method, path in [
            ("GET", "/api/v1/forge/stats"),
            ("GET", "/api/v1/forge/notifications/rules"),
            ("DELETE", f"/api/v1/forge/sessions/{session.id}"),
            ("GET", "/api/v1/forge/feature-flags"),
        ]:
            response = client.request(method, path, headers=auth)
            assert response.status_code == 403, (method, path)
            assert "may not call" in response.json()["detail"]
        assert remote.called is False

    async def test_the_edge_checks_the_scope(self, guild) -> None:
        client, forge, _, auth = guild
        peer = await forge.session("peer")
        response = client.post(f"/api/v1/forge/sessions/{peer.id}/stop", headers=auth)
        assert response.status_code == 403
        assert "forge:session:lifecycle" in response.json()["detail"]

    async def test_replay_websocket_is_refused(self, guild) -> None:
        from starlette.websockets import WebSocketDisconnect

        client, _, session, auth = guild
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            client.websocket_connect(f"/api/v1/forge/sessions/{session.id}/replay", headers=auth),
        ):
            pass
        assert closed.value.code == 4403


class TestMcpForward:
    async def test_mcp_reaches_the_local_node(self, guild) -> None:
        client, _, session, auth = guild
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_sessions", "arguments": {}},
        }
        response = client.post("/api/v1/forge/mcp", json=message, headers={**auth, **JSON_HEADERS})
        assert response.status_code == 200
        sessions = response.json()["result"]["structuredContent"]["sessions"]
        assert [item["id"] for item in sessions] == [str(session.id)]

    async def test_origin_and_status_pass_through(self, guild) -> None:
        client, _, _, _ = guild
        user = {**JSON_HEADERS, "x-auth-user-id": OWNER, "x-auth-tenant": TENANT}
        response = client.post(
            "/api/v1/forge/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={**user, "Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        note = client.post(
            "/api/v1/forge/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=user,
        )
        assert note.status_code == 202

    async def test_get_is_not_offered(self, guild) -> None:
        client, _, _, _ = guild
        assert client.get("/api/v1/forge/mcp").status_code == 405

    @respx.mock
    async def test_session_token_cannot_reach_another_nodes_mcp(self, guild) -> None:
        client, _, _, auth = guild
        remote = respx.post(f"{REMOTE}/api/v1/forge/mcp").mock(return_value=Response(200, json={}))
        response = client.post(
            "/api/v1/forge/mcp",
            params={"instance_id": "remote"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={**auth, **JSON_HEADERS},
        )
        assert response.status_code == 403 and remote.called is False

    @respx.mock
    async def test_a_user_can_pick_another_node(self, guild) -> None:
        client, _, _, _ = guild
        remote = respx.post(f"{REMOTE}/api/v1/forge/mcp").mock(
            return_value=Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        )
        headers = {**JSON_HEADERS, "x-auth-user-id": OWNER, "x-auth-tenant": TENANT}
        headers["MCP-Protocol-Version"] = "2025-06-18"
        response = client.post(
            "/api/v1/forge/mcp",
            params={"instance_id": "remote"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers=headers,
        )
        assert response.status_code == 200 and response.json()["result"] == {}
        sent = remote.calls.last.request
        assert json.loads(sent.content)["method"] == "ping"
        assert sent.headers["mcp-protocol-version"] == "2025-06-18"
        assert sent.headers["content-type"] == "application/json"
        assert "instance_id" not in str(sent.url)


class TestNiuuPrincipal:
    def _app(self) -> TestClient:
        app = FastAPI()
        app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())

        @app.get("/api/v1/forge/whoami")
        @app.get("/api/v1/niuu/whoami")
        async def whoami(request: Request) -> dict:
            principal = await extract_principal(request)
            return {
                "user": principal.user_id,
                "tenant": principal.tenant_id,
                "roles": principal.roles,
                "token_use": principal.token_use,
                "session": principal.bound_session_id,
                "scopes": list(principal.scopes),
            }

        return TestClient(app)

    async def test_session_token_principal(self, guild) -> None:
        _, _, session, auth = guild
        body = self._app().get("/api/v1/forge/whoami", headers=auth).json()
        assert body["user"] == OWNER and body["tenant"] == TENANT
        assert body["token_use"] == FORGE_SESSION_TOKEN_USE
        assert body["session"] == str(session.id)
        assert body["roles"] == ["volundr:developer"]
        # a session token wins over forwarded identity headers
        spoofed = {**auth, "x-auth-user-id": "admin", "x-auth-roles": "volundr:admin"}
        assert self._app().get("/api/v1/forge/whoami", headers=spoofed).json()["user"] == OWNER
        via_query = self._app().get(
            "/api/v1/forge/whoami", params={"token": auth["Authorization"].split()[1]}
        )
        assert via_query.json()["token_use"] == FORGE_SESSION_TOKEN_USE

    async def test_session_token_outside_the_forge_api_is_refused(self, guild) -> None:
        _, _, _, auth = guild
        response = self._app().get("/api/v1/niuu/whoami", headers=auth)
        assert response.status_code == 403 and "Forge API" in response.json()["detail"]

    def test_other_callers_are_unchanged(self) -> None:
        client = self._app()
        assert client.get("/api/v1/niuu/whoami").json()["user"] == "dev-user"
        headers = {"Authorization": "Bearer opaque", "x-auth-user-id": "alice"}
        body = client.get("/api/v1/niuu/whoami", headers=headers).json()
        assert body["user"] == "alice" and body["token_use"] == ""
