"""Broker side of the Forge MCP: loopback routes, notify via the durable log, Forge proxying."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request

from niuu.domain.notifications import draft_from_log_payload, notification_id, turn_dedupe_key
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.forge_mcp.forge_client import HttpForgeClient
from skuld.forge_mcp.host import engine_for_adapter
from skuld.forge_mcp.routes import register_forge_mcp_routes

SESSION_ID = str(uuid.uuid4())
PEER_ID = str(uuid.uuid4())
FORGE = "http://forge.test"
SERVICE_TOKEN = "svc-broker-credential"
LOG_PATH = f"/api/v1/forge/sessions/{SESSION_ID}/log"
#: Skuld only reads a session token's claims (Forge verifies it), so any key will do.
_TEST_SIGNING_KEY = "test-only-signing-key-that-is-32-bytes"


def _session_token(*grant_scopes: str) -> str:
    """A forge_session-shaped token with the default scopes plus ``grant_scopes``."""
    return jwt.encode(
        {
            "sub": "owner-1",
            "token_use": "forge_session",
            "scopes": ["forge:notify", "forge:session:read", *grant_scopes],
            "workload_session_id": SESSION_ID,
            "exp": 4_102_444_800,
        },
        _TEST_SIGNING_KEY,
        algorithm="HS256",
    )


MESSAGE_TOKEN = _session_token("forge:session:message")
FULL_TOKEN = _session_token("forge:session:message", "forge:session:lifecycle")


class FakeForge:
    """A programmable Forge REST double served through httpx.MockTransport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.log_status = 201
        self.log_conflicts: list[int] = []
        self.logged: list[dict[str, Any]] = []
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST" and request.url.path == LOG_PATH:
            if self.log_status >= 300:
                return httpx.Response(self.log_status, json={"detail": "forge down"})
            entries = json.loads(request.content)["entries"]
            self.logged.extend(entries)
            return httpx.Response(
                201,
                json={
                    "submitted": len(entries),
                    "latest_seq": max(e["seq"] for e in entries),
                    "conflicts": self.log_conflicts,
                },
            )
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"detail": f"no route {request.url.path}"})
        return route(request)


def _broker(tmp_path, forge: FakeForge | None, **forge_mcp: Any) -> Broker:
    settings = SkuldSettings(
        session={
            "id": SESSION_ID,
            "name": "notif-test",
            "workspace_dir": str(tmp_path / "ws"),
            "model": "claude-opus-5",
        },
        volundr_api_url=FORGE if forge else "",
        external_api_token=SERVICE_TOKEN,
        event_log_flush_interval_ms=10,
        forge_mcp={
            "runtime_dir": str(tmp_path / "rt"),
            "notify_confirm_timeout_s": 0.5,
            **forge_mcp,
        },
    )
    broker = Broker(settings=settings)
    broker._save_conversation_history = lambda: None  # type: ignore[method-assign]
    if forge is not None:
        headers = broker._build_auth_headers()
        broker._http_client = httpx.AsyncClient(
            base_url=FORGE, transport=httpx.MockTransport(forge.handler), headers=headers
        )
        broker._http_client_jwt = broker._user_jwt
        broker._http_client_auth_header = headers.get("Authorization", "")
    return broker


def _app(broker: Broker) -> FastAPI:
    app = FastAPI()

    def guard(request: Request) -> None:
        if not broker._session_runtime.accepts(request.headers.get("authorization")):
            raise HTTPException(401)

    register_forge_mcp_routes(app, lambda: broker, dependencies=[Depends(guard)])
    return app


def _client(broker: Broker, *, authorized: bool = True) -> httpx.AsyncClient:
    headers = {"authorization": broker._session_runtime.authorization} if authorized else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(broker)), base_url="http://broker", headers=headers
    )


async def _call(broker: Broker, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with _client(broker) as client:
        response = await client.post(f"/api/forge-mcp/tools/{tool}", json=arguments)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def forge() -> FakeForge:
    return FakeForge()


@pytest.fixture
async def flushing(tmp_path, forge):
    """A broker whose durable-log worker is running against the fake Forge."""
    broker = _broker(tmp_path, forge)
    broadcasts: list[dict] = []

    async def capture(frame: dict) -> None:
        broadcasts.append(frame)

    broker._channels.broadcast = capture  # type: ignore[method-assign]
    broker._event_log_task = asyncio.create_task(broker._event_log_flush_loop())
    broker.broadcasts = broadcasts  # type: ignore[attr-defined]
    yield broker
    broker._event_log_stopping = True
    broker._event_log_task.cancel()
    await asyncio.gather(broker._event_log_task, return_exceptions=True)


NOTIFY = {
    "kind": "milestone",
    "severity": "success",
    "title": "Tests green",
    "body": "All 312 pass.",
    "links": [{"label": "PR", "url": "https://git.example/pr/7", "kind": "pr"}],
}


class TestRoutes:
    async def test_tools_require_the_loopback_secret(self, tmp_path) -> None:
        broker = _broker(tmp_path, None)
        async with _client(broker, authorized=False) as client:
            assert (await client.get("/api/forge-mcp/tools")).status_code == 401
            assert (await client.post("/api/forge-mcp/tools/notify", json={})).status_code == 401
        async with _client(broker) as client:
            listed = await client.get("/api/forge-mcp/tools")
        assert listed.status_code == 200
        assert "notify" in [tool["name"] for tool in listed.json()["tools"]]

    async def test_bad_requests(self, tmp_path) -> None:
        broker = _broker(tmp_path, None)
        async with _client(broker) as client:
            unknown = await client.post("/api/forge-mcp/tools/delete_session", json={})
            not_object = await client.post("/api/forge-mcp/tools/environment", json=[1])
            not_json = await client.post("/api/forge-mcp/tools/environment", content=b"{")
            empty = await client.post("/api/forge-mcp/tools/list_sessions", content=b"")
        assert unknown.status_code == 404
        assert not_object.status_code == 400
        assert not_json.status_code == 400
        assert empty.status_code == 200 and empty.json()["isError"] is True  # no Forge URL

    async def test_disabled_forge_mcp_answers_404(self, tmp_path) -> None:
        broker = _broker(tmp_path, None, enabled=False)
        async with _client(broker) as client:
            assert (await client.get("/api/forge-mcp/tools")).status_code == 404

    async def test_module_app_guards_forge_mcp(self, module_broker_runtime) -> None:
        from skuld import broker as bmod

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bmod.app), base_url="http://broker"
        ) as client:
            denied = await client.get("/api/forge-mcp/tools")
            allowed = await client.get(
                "/api/forge-mcp/tools",
                headers={"authorization": module_broker_runtime.authorization},
            )
            message = await client.post("/api/message", json={"session_id": "x", "content": "y"})
            hooks = await client.post("/api/claude/hooks", json={"hook_event_name": "Stop"})
        assert denied.status_code == 401
        assert allowed.status_code == 200
        assert message.status_code == 401
        assert hooks.status_code == 401


class TestNotify:
    async def test_committed_once_forge_stores_the_turn(self, flushing, forge) -> None:
        result = await _call(flushing, "notify", NOTIFY)

        assert result["isError"] is False
        payload = result["structuredContent"]
        assert payload["state"] == "committed"
        turn_id = payload["turn_id"]
        assert turn_id.startswith("nt_") and len(turn_id) == 35
        expected_id = notification_id(turn_dedupe_key(SESSION_ID, turn_id, "milestone"))
        assert payload["notification_id"] == str(expected_id)
        # Forge got exactly the shared-contract turn at the reported seq.
        [entry] = [e for e in forge.logged if e["payload"].get("type") == "conversation.turn"]
        assert entry["seq"] == payload["session_seq"]
        parsed = draft_from_log_payload(entry["payload"])
        assert parsed is not None and parsed[0] == turn_id
        assert parsed[1].title == "Tests green" and parsed[1].links[0].kind.value == "pr"
        turn = entry["payload"]["turn"]
        assert turn["parts"][0]["name"] == "forge_notification"
        assert "final_output" not in turn["metadata"]
        # Same append path as present-file: in history and broadcast live.
        assert flushing._conversation_turns[-1].id == turn_id
        assert flushing.broadcasts[-1]["turn"]["id"] == turn_id

    async def test_pending_while_forge_is_down_then_commits(self, flushing, forge) -> None:
        forge.log_status = 503
        flushing._settings.forge_mcp.notify_confirm_timeout_s = 0.1

        result = await _call(flushing, "notify", NOTIFY)

        assert result["structuredContent"]["state"] == "pending"
        assert forge.logged == []
        forge.log_status = 201
        for _ in range(100):
            if forge.logged:
                break
            await asyncio.sleep(0.02)
        assert forge.logged[0]["seq"] == result["structuredContent"]["session_seq"]

    async def test_a_conflicting_seq_is_reported(self, flushing, forge) -> None:
        forge.log_conflicts = [1]
        result = await _call(flushing, "notify", NOTIFY)
        assert result["isError"] is True
        assert "not stored" in result["content"][0]["text"]

    async def test_no_durable_log_means_no_notification(self, tmp_path) -> None:
        broker = _broker(tmp_path, None)
        result = await _call(broker, "notify", NOTIFY)
        assert result["isError"] is True
        assert "no durable Forge log" in result["content"][0]["text"]
        assert broker._conversation_turns == []

    async def test_dropped_entry_resolves_false(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge)
        broker._settings.event_log_max_buffer = 1
        entry = broker._enqueue_event_log({"type": "a"})
        receipt = broker._watch_event_log_entry(entry)
        broker._enqueue_event_log({"type": "b"})  # overflow drops the watched entry
        assert await broker._await_event_log_receipt(receipt, 1.0) is False

    async def test_watching_an_already_dropped_entry(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge)
        entry = broker._enqueue_event_log({"type": "a"})
        broker._event_log_buffer.clear()  # dropped before it could be watched
        receipt = broker._watch_event_log_entry(entry)
        assert await broker._await_event_log_receipt(receipt, 1.0) is False
        assert broker._event_log_receipts == {}


class TestEnvironment:
    async def test_describes_the_session_without_secrets(self, tmp_path, forge) -> None:
        forge.routes[("GET", f"/api/v1/forge/sessions/{SESSION_ID}")] = lambda _r: httpx.Response(
            200,
            json={
                "id": SESSION_ID,
                "instance_id": "thor",
                "coordination": {"project_id": "proj-42"},
            },
        )
        broker = _broker(tmp_path, forge, token=FULL_TOKEN, grants=["message"])
        broker._session_tools()  # materialize the skill like a started broker

        result = await _call(broker, "environment", {})

        env = result["structuredContent"]
        assert env["session"] == {"id": SESSION_ID, "name": "notif-test"}
        assert env["engine"] == "claude" and env["model"] == "claude-opus-5"
        assert env["forge_url"] == FORGE
        assert env["project_id"] == "proj-42" and env["instance_id"] == "thor"
        assert env["forge_reachable"] is True
        assert "forge" in env["mcp_servers"]
        assert env["skills"] == ["forge-notify"]
        assert env["grants"] == ["message"]  # explicit grants narrowed the token's
        assert "send_message" in env["tools"] and "create_session" not in env["tools"]
        assert {"revision", "build"} <= set(env["runtime"])
        assert env["forge_credential"]["kind"] == "forge_session"
        assert env["forge_credential"]["session_id"] == SESSION_ID
        assert "forge:session:lifecycle" in env["forge_credential"]["scopes"]
        text = json.dumps(result)
        secret = broker._session_runtime.authorization.removeprefix("Bearer ")
        for leaked in (SERVICE_TOKEN, FULL_TOKEN, secret):
            assert leaked not in text

    async def test_grants_come_from_the_token(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge, token=FULL_TOKEN)
        env = (await _call(broker, "environment", {}))["structuredContent"]
        assert env["grants"] == ["lifecycle", "message"]

    async def test_explicit_grants_never_widen(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge, token=MESSAGE_TOKEN, grants=["message", "lifecycle"])
        env = (await _call(broker, "environment", {}))["structuredContent"]
        assert env["grants"] == ["message"]
        result = await _call(broker, "start_session", {"session_id": PEER_ID})
        assert result["isError"] is True and "'lifecycle' grant" in result["content"][0]["text"]

    async def test_no_token_means_no_grants(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge, grants=["message", "lifecycle"])
        env = (await _call(broker, "environment", {}))["structuredContent"]
        assert env["grants"] == []
        assert env["forge_credential"]["kind"] == "broker"

    async def test_an_explicitly_empty_env_value_narrows_to_nothing(self, tmp_path, forge) -> None:
        broker = _broker(tmp_path, forge, token=FULL_TOKEN, grants="")
        env = (await _call(broker, "environment", {}))["structuredContent"]
        assert env["grants"] == []

    async def test_reports_an_unreachable_forge(self, tmp_path, forge) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        forge.routes[("GET", f"/api/v1/forge/sessions/{SESSION_ID}")] = down
        broker = _broker(tmp_path, forge)
        env = (await _call(broker, "environment", {}))["structuredContent"]
        assert env["forge_reachable"] is False and "refused" in env["forge_error"]
        assert env["project_id"] is None

    async def test_without_forge_url(self, tmp_path) -> None:
        env = (await _call(_broker(tmp_path, None), "environment", {}))["structuredContent"]
        assert env["forge_reachable"] is False and env["forge_url"] is None

    @pytest.mark.parametrize(
        ("adapter", "engine"),
        [
            ("skuld.transports.tmux_interactive.TmuxInteractiveTransport", "claude"),
            ("skuld.transports.codex_ws.CodexWebSocketTransport", "codex"),
            ("skuld.transports.grok.GrokACPTransport", "grok"),
            ("acme.transports.custom.Thing", "custom"),
        ],
    )
    def test_engine_names(self, adapter, engine) -> None:
        assert engine_for_adapter(adapter) == engine


class TestForgeProxy:
    async def test_scoped_token_is_used_for_mcp_calls_only(self, flushing, forge) -> None:
        flushing._settings.forge_mcp.token = MESSAGE_TOKEN
        flushing._forge_mcp_toolbox_cache = None
        forge.routes[("GET", "/api/v1/forge/sessions")] = lambda _r: httpx.Response(200, json=[])

        await _call(flushing, "list_sessions", {"scope": "local"})
        await _call(flushing, "notify", NOTIFY)

        listed = next(r for r in forge.requests if r.url.path == "/api/v1/forge/sessions")
        assert listed.headers["authorization"] == f"Bearer {MESSAGE_TOKEN}"
        assert listed.url.params["scope"] == "local"
        log_post = next(r for r in forge.requests if r.url.path == LOG_PATH)
        assert log_post.headers["authorization"] == f"Bearer {SERVICE_TOKEN}"

    async def test_broker_credential_without_scoped_token(self, tmp_path, forge) -> None:
        forge.routes[("GET", f"/api/v1/forge/sessions/{PEER_ID}")] = lambda _r: httpx.Response(
            200, json={"id": PEER_ID, "status": "running"}
        )
        broker = _broker(tmp_path, forge)
        result = await _call(broker, "get_session", {"session_id": PEER_ID})
        assert result["structuredContent"]["session"]["status"] == "running"
        assert forge.requests[-1].headers["authorization"] == f"Bearer {SERVICE_TOKEN}"

    async def test_routes_and_bodies(self, tmp_path, forge) -> None:
        seen: dict[str, httpx.Request] = {}

        def record(name: str, response: Any) -> Callable[[httpx.Request], httpx.Response]:
            def handle(request: httpx.Request) -> httpx.Response:
                seen[name] = request
                return httpx.Response(200, json=response)

            return handle

        base = f"/api/v1/forge/sessions/{PEER_ID}"
        forge.routes.update(
            {
                ("GET", "/api/v1/forge/notifications"): record("notifications", {"items": []}),
                ("GET", f"{base}/conversation"): record("conversation", {"turns": []}),
                ("POST", f"{base}/messages"): record("message", {"status": "delivered"}),
                ("GET", f"{base}/message-deliveries/r-1"): record("delivery", {"status": "ok"}),
                ("POST", "/api/v1/forge/sessions"): record("create", {"id": PEER_ID}),
                ("POST", f"{base}/start"): record("start", {"id": PEER_ID}),
                ("POST", f"{base}/stop"): record("stop", {"id": PEER_ID}),
            }
        )
        broker = _broker(tmp_path, forge, token=FULL_TOKEN)
        calls = [
            ("list_notifications", {"kinds": ["attention"]}),
            ("session_transcript", {"session_id": PEER_ID, "instance_id": "node-b"}),
            ("send_message", {"session_id": PEER_ID, "content": "hi", "request_id": "r-1"}),
            ("message_status", {"session_id": PEER_ID, "request_id": "r-1"}),
            ("create_session", {"name": "helper", "local_path": "/srv/app"}),
            ("start_session", {"session_id": PEER_ID}),
            ("stop_session", {"session_id": PEER_ID}),
        ]
        for tool, arguments in calls:
            result = await _call(broker, tool, arguments)
            assert result["isError"] is False, (tool, result)

        assert seen["notifications"].url.params["kind"] == "attention"
        assert seen["conversation"].url.params["detail"] == "shallow"
        assert seen["conversation"].url.params["instance_id"] == "node-b"
        assert json.loads(seen["message"].content) == {"content": "hi", "request_id": "r-1"}
        assert json.loads(seen["create"].content)["source"]["type"] == "local_mount"

    async def test_forge_errors_surface_as_tool_errors(self, tmp_path, forge) -> None:
        forge.routes[("GET", f"/api/v1/forge/sessions/{PEER_ID}")] = lambda _r: httpx.Response(
            403, json={"detail": "not your session"}
        )
        broker = _broker(tmp_path, forge)
        result = await _call(broker, "get_session", {"session_id": PEER_ID})
        assert result["isError"] is True
        assert "403: not your session" in result["content"][0]["text"]


class TestHttpForgeClientShapes:
    async def test_non_json_and_wrong_shapes_are_errors(self) -> None:
        from niuu.forge_mcp.models import ForgeApiError

        responses = iter(
            [
                httpx.Response(200, content=b"<html>"),
                httpx.Response(200, json={"not": "a list"}),
                httpx.Response(200, json=[1]),
                httpx.Response(500, content=b"boom"),
                httpx.Response(204),
            ]
        )
        client = httpx.AsyncClient(
            base_url=FORGE, transport=httpx.MockTransport(lambda _r: next(responses))
        )

        async def provider() -> httpx.AsyncClient:
            return client

        forge_client = HttpForgeClient(http_client=provider, token="", timeout_s=1.0)
        with pytest.raises(ForgeApiError, match="non-JSON"):
            await forge_client.get_session(PEER_ID, instance_id=None)
        with pytest.raises(ForgeApiError, match="unexpected response shape"):
            await forge_client.list_sessions({})
        with pytest.raises(ForgeApiError, match="unexpected response shape"):
            await forge_client.list_notifications({})
        with pytest.raises(ForgeApiError) as excinfo:
            await forge_client.stop_session(PEER_ID, instance_id=None)
        assert excinfo.value.status == 500 and excinfo.value.detail == "boom"
        assert await forge_client.start_session(PEER_ID, instance_id=None) == {}
