"""The Forge-hosted MCP endpoint over Streamable HTTP (JSON response mode)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from niuu.domain.notifications import NotificationDraft
from niuu.forge_mcp import TOOL_NAMES, ForgeApiError
from niuu.forge_mcp.http import LATEST_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS
from tests.support.forge_session import OWNER, PREFIX, TENANT, bearer, build_forge_app
from volundr.adapters.inbound.rest_forge_mcp import CallerForgeMcpHost
from volundr.config import ForgeMcpHttpConfig
from volundr.domain.models import Principal

MCP = f"{PREFIX}/mcp"
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
OWNER_HEADERS = {"x-auth-user-id": OWNER, "x-auth-tenant": TENANT}


@pytest.fixture
def forge():
    return build_forge_app()


def rpc(client: TestClient, method: str, params: Any = None, *, headers=None, request_id=1):
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return client.post(MCP, json=message, headers={**JSON_HEADERS, **(headers or {})})


def call(client: TestClient, tool: str, arguments: dict, *, headers=None) -> dict:
    response = rpc(client, "tools/call", {"name": tool, "arguments": arguments}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def _started(forge, *grants: str):
    session = await forge.session("self")
    if grants:
        stored = await forge.sessions.get(session.id)
        config = {"forge_mcp": {"grants": list(grants)}}
        await forge.sessions.update(stored.model_copy(update={"workload_config": config}))
    credential = await forge.start(session)
    return session, bearer(credential.token)


class TestProtocol:
    @pytest.mark.parametrize("version", SUPPORTED_PROTOCOL_VERSIONS)
    def test_initialize_echoes_a_supported_version(self, forge, version) -> None:
        response = rpc(forge.client, "initialize", {"protocolVersion": version})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        result = response.json()["result"]
        assert result["protocolVersion"] == version
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        assert result["serverInfo"]["name"] == "forge"
        assert "idempotency_key" in result["instructions"]
        assert "mcp-session-id" not in response.headers  # stateless

    def test_initialize_offers_the_latest_for_an_unknown_version(self, forge) -> None:
        result = rpc(forge.client, "initialize", {"protocolVersion": "1999-01-01"}).json()
        assert result["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION

    def test_notifications_and_responses_are_accepted(self, forge) -> None:
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        response = forge.client.post(MCP, json=note, headers=JSON_HEADERS)
        assert response.status_code == 202 and response.content == b""
        reply = {"jsonrpc": "2.0", "id": 9, "result": {}}
        assert forge.client.post(MCP, json=reply, headers=JSON_HEADERS).status_code == 202

    def test_ping(self, forge) -> None:
        assert rpc(forge.client, "ping").json() == {"jsonrpc": "2.0", "id": 1, "result": {}}

    def test_unknown_method(self, forge) -> None:
        error = rpc(forge.client, "resources/list").json()["error"]
        assert error["code"] == -32601

    def test_tools_list(self, forge) -> None:
        tools = rpc(forge.client, "tools/list").json()["result"]["tools"]
        assert [tool["name"] for tool in tools] == list(TOOL_NAMES)
        notify = next(tool for tool in tools if tool["name"] == "notify")
        assert "idempotency_key" in notify["inputSchema"]["required"]

    def test_unknown_tool_is_a_protocol_error(self, forge) -> None:
        response = rpc(forge.client, "tools/call", {"name": "rm_rf", "arguments": {}})
        assert response.json()["error"]["code"] == -32602

    @pytest.mark.parametrize(
        ("params", "code"),
        [({"arguments": {}}, -32602), ({"name": "ping", "arguments": []}, -32602)],
    )
    def test_bad_tool_calls(self, forge, params, code) -> None:
        assert rpc(forge.client, "tools/call", params).json()["error"]["code"] == code

    def test_params_must_be_an_object(self, forge) -> None:
        assert rpc(forge.client, "ping", [1, 2]).json()["error"]["code"] == -32602

    @pytest.mark.parametrize(
        ("body", "status", "code"),
        [
            (b"{not json", 400, -32700),
            (b'[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]', 400, -32600),
            (b'{"jsonrpc": "1.0", "id": 1, "method": "ping"}', 400, -32600),
            (b'{"jsonrpc": "2.0", "id": 1}', 400, -32600),
            (b'{"jsonrpc": "2.0", "id": 1, "method": 7}', 400, -32600),
        ],
    )
    def test_malformed_messages(self, forge, body, status, code) -> None:
        response = forge.client.post(MCP, content=body, headers=JSON_HEADERS)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code

    def test_get_and_delete_are_not_offered(self, forge) -> None:
        for method in ("GET", "DELETE"):
            response = forge.client.request(method, MCP)
            assert response.status_code == 405 and response.headers["allow"] == "POST"


class TestTransportChecks:
    def test_foreign_browser_origin_is_forbidden(self, forge) -> None:
        response = rpc(forge.client, "ping", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403 and "Origin not allowed" in response.text

    def test_configured_origin_is_allowed(self) -> None:
        config = ForgeMcpHttpConfig(allowed_origins=["https://forge.example/"])
        client = build_forge_app(mcp_config=config).client
        response = client.post(
            MCP,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={**JSON_HEADERS, "Origin": "https://forge.example"},
        )
        assert response.status_code == 200

    def test_accept_must_allow_json(self, forge) -> None:
        response = rpc(forge.client, "ping", headers={"Accept": "text/event-stream"})
        assert response.status_code == 406
        assert rpc(forge.client, "ping", headers={"Accept": "*/*"}).status_code == 200

    def test_content_type_must_be_json(self, forge) -> None:
        response = forge.client.post(
            MCP,
            content=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 415

    def test_protocol_version_header(self, forge) -> None:
        good = rpc(forge.client, "ping", headers={"MCP-Protocol-Version": "2025-06-18"})
        assert good.status_code == 200
        bad = rpc(forge.client, "ping", headers={"MCP-Protocol-Version": "2020-01-01"})
        assert bad.status_code == 400 and "Unsupported" in bad.text

    def test_body_is_bounded(self) -> None:
        client = build_forge_app(mcp_config=ForgeMcpHttpConfig(max_body_bytes=1024)).client
        big = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "x" * 2048}}
        assert client.post(MCP, json=big, headers=JSON_HEADERS).status_code == 413

        def chunks():  # no Content-Length: the stream itself is bounded
            yield b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"pad":"'
            yield b"x" * 2048
            yield b'"}}'

        streamed = client.post(MCP, content=chunks(), headers=JSON_HEADERS)
        assert streamed.status_code == 413


class TestToolsAsASessionToken:
    async def test_environment_reports_the_session_credential(self, forge) -> None:
        session, auth = await _started(forge, "message")
        env = call(forge.client, "environment", {}, headers=auth)["structuredContent"]
        assert env["caller"]["credential"] == "forge_session"
        assert env["caller"]["session_id"] == str(session.id)
        assert "forge:session:message" in env["caller"]["scopes"]
        assert env["grants"] == ["message"]
        assert env["transport"] == "streamable-http" and env["notify"] == "feed"
        assert auth["Authorization"].split()[1] not in str(env)

    async def test_notify_lands_in_the_feed_as_agent(self, forge) -> None:
        session, auth = await _started(forge)
        arguments = {"kind": "milestone", "title": "Shipped", "idempotency_key": "ship-1"}
        first = call(forge.client, "notify", arguments, headers=auth)
        assert first["isError"] is False
        recorded = first["structuredContent"]
        assert recorded["session_id"] == str(session.id) and recorded["source"] == "agent"
        again = call(forge.client, "notify", arguments, headers=auth)["structuredContent"]
        assert again["notification_id"] == recorded["notification_id"]

        feed = forge.client.get(f"{PREFIX}/notifications", headers=OWNER_HEADERS).json()
        assert [item["title"] for item in feed["items"]] == ["Shipped"]
        assert feed["items"][0]["source"] == "agent"

    async def test_notify_about_another_session_is_denied(self, forge) -> None:
        _, auth = await _started(forge)
        peer = await forge.session("peer")
        result = call(
            forge.client,
            "notify",
            {"kind": "info", "title": "x", "idempotency_key": "k", "session_id": str(peer.id)},
            headers=auth,
        )
        assert result["isError"] is True and "403" in result["content"][0]["text"]

    async def test_list_sessions_is_owner_scoped(self, forge) -> None:
        session, auth = await _started(forge)
        await forge.session("theirs", owner="someone-else")
        result = call(forge.client, "list_sessions", {}, headers=auth)["structuredContent"]
        assert [item["id"] for item in result["sessions"]] == [str(session.id)]

    async def test_grant_denial(self, forge) -> None:
        _, auth = await _started(forge)
        peer = await forge.session("peer")
        result = call(
            forge.client,
            "send_message",
            {"session_id": str(peer.id), "content": "hi"},
            headers=auth,
        )
        assert result["isError"] is True and "'message' grant" in result["content"][0]["text"]

    async def test_forge_still_enforces_what_the_tool_offers(self, forge) -> None:
        _, auth = await _started(forge, "lifecycle")
        foreign = await forge.session("foreign", owner="someone-else")
        result = call(forge.client, "stop_session", {"session_id": str(foreign.id)}, headers=auth)
        assert result["isError"] is True and "same owner" in result["content"][0]["text"]

    async def test_other_nodes_are_refused(self, forge) -> None:
        _, auth = await _started(forge)
        peer = await forge.session("peer")
        result = call(
            forge.client,
            "get_session",
            {"session_id": str(peer.id), "instance_id": "node-b"},
            headers=auth,
        )
        assert result["isError"] is True
        assert "only on the node that serves it" in result["content"][0]["text"]

    async def test_query_token_is_carried_to_tool_calls(self, forge) -> None:
        session, auth = await _started(forge)
        token = auth["Authorization"].split()[1]
        response = forge.client.post(
            MCP,
            params={"token": token},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_sessions", "arguments": {}},
            },
            headers=JSON_HEADERS,
        )
        sessions = response.json()["result"]["structuredContent"]["sessions"]
        assert [item["id"] for item in sessions] == [str(session.id)]

    def test_invalid_session_token_is_401(self, forge) -> None:
        response = rpc(forge.client, "ping", headers={"Authorization": "Bearer x.y.z"})
        assert response.status_code == 200  # opaque bearer: not a session token
        forged = "eyJhbGciOiJIUzI1NiJ9.eyJ0b2tlbl91c2UiOiJmb3JnZV9zZXNzaW9uIn0.c2lnbmF0dXJl"
        assert rpc(forge.client, "ping", headers=bearer(forged)).status_code == 401


class TestToolsAsAUser:
    async def test_user_gets_every_tool_and_notifies_as_operator(self, forge) -> None:
        session = await forge.session("mine")
        env = call(forge.client, "environment", {}, headers=OWNER_HEADERS)["structuredContent"]
        assert env["caller"] == {
            "user_id": OWNER,
            "tenant_id": TENANT,
            "credential": "user",
            "session_id": None,
            "scopes": None,
        }
        assert env["grants"] == ["lifecycle", "message"]

        missing = call(
            forge.client,
            "notify",
            {"kind": "info", "title": "x", "idempotency_key": "k"},
            headers=OWNER_HEADERS,
        )
        assert missing["isError"] is True and "needs session_id" in missing["content"][0]["text"]
        recorded = call(
            forge.client,
            "notify",
            {"kind": "info", "title": "Hi", "idempotency_key": "k", "session_id": str(session.id)},
            headers=OWNER_HEADERS,
        )["structuredContent"]
        assert recorded["source"] == "operator"


class TestCallerHost:
    class _Client:
        def __init__(self) -> None:
            self.keys: list[str] = []

        async def submit_notification(self, session_id, draft, *, idempotency_key, instance_id):
            self.keys.append(idempotency_key)
            return {"id": "n1", "session_seq": None}

    async def test_notify_derives_a_stable_key(self) -> None:
        client = self._Client()
        principal = Principal(
            user_id=OWNER,
            email="",
            tenant_id=TENANT,
            roles=[],
            token_use="forge_session",
            bound_session_id=str(uuid.uuid4()),
        )
        host = CallerForgeMcpHost(principal, client)  # type: ignore[arg-type]
        draft = NotificationDraft(kind="milestone", title="t")
        first = await host.notify(draft)
        await host.notify(draft)
        await host.notify(NotificationDraft(kind="milestone", title="other"))
        assert first == {
            "notification_id": "n1",
            "turn_id": None,
            "session_seq": None,
            "state": "committed",
        }
        assert client.keys[0] == client.keys[1] != client.keys[2]
        assert client.keys[0].startswith("mcp-")

    async def test_notify_without_a_session(self) -> None:
        host = CallerForgeMcpHost(
            Principal(user_id="u", email="", tenant_id="t", roles=[]),
            self._Client(),  # type: ignore[arg-type]
        )
        with pytest.raises(ForgeApiError, match="needs a session"):
            await host.notify(NotificationDraft(kind="info", title="t"))
