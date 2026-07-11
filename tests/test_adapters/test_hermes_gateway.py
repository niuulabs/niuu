"""Contract tests for the Hermes resident session adapter."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
import websockets

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.domain.models import SecretType
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.hermes_gateway import (
    HermesChatConnection,
    HermesGatewayError,
    HermesResidentSessionController,
    ensure_hermes_dashboard_token,
)
from volundr.domain.models import ResidentBackend, ResidentEngine, ResidentRuntime


class _Route:
    def __init__(self, port: int) -> None:
        self.port = port

    def resident_proxy_target(self, _runtime: ResidentRuntime) -> SessionProxyTarget:
        return SessionProxyTarget(
            service_url=f"http://hermes.test:{self.port}",
            connect_host="127.0.0.1",
            connect_port=self.port,
        )


def _runtime() -> ResidentRuntime:
    return ResidentRuntime(
        id=UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Hermes",
        model="openrouter/anthropic/claude-sonnet-4.6",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.HERMES,
        profile_id="nemohermes-openshell",
    )


async def _reply(websocket, request: dict, result: dict) -> None:
    await websocket.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}))


@pytest.mark.asyncio
async def test_dashboard_token_is_generated_once_and_is_the_only_stored_secret() -> None:
    store = MemoryCredentialStore()
    runtime = _runtime()

    first = await ensure_hermes_dashboard_token(store, runtime)
    second = await ensure_hermes_dashboard_token(store, runtime)

    assert first == second
    assert await store.get_value("resident", str(runtime.id), "hermes-dashboard") == {
        "session_token": first
    }


@pytest.mark.asyncio
async def test_incomplete_dashboard_credential_fails_closed() -> None:
    store = MemoryCredentialStore()
    runtime = _runtime()
    await store.store(
        "resident",
        str(runtime.id),
        "hermes-dashboard",
        SecretType.API_KEY,
        {"not_the_token": "value"},
    )

    with pytest.raises(RuntimeError, match="credential is incomplete"):
        await ensure_hermes_dashboard_token(store, runtime)


@pytest.mark.asyncio
async def test_session_lifecycle_uses_persistent_keys_and_deterministic_uuids() -> None:
    persistent_key = "20260711_120000_abc123"
    created_key = "20260711_120100_def456"
    paths: list[str] = []
    requests: list[dict] = []

    async def gateway(websocket) -> None:
        paths.append(websocket.request.path)
        await websocket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"type": "gateway.ready", "payload": {}},
                }
            )
        )
        async for raw in websocket:
            request = json.loads(raw)
            requests.append(request)
            if request["method"] == "session.list":
                result = {
                    "sessions": [
                        {
                            "id": persistent_key,
                            "title": "Persistent work",
                            "started_at": datetime(2026, 7, 11, tzinfo=UTC).timestamp(),
                            "message_count": 4,
                        }
                    ]
                }
            elif request["method"] == "session.create":
                result = {
                    "session_id": "live-created",
                    "stored_session_id": created_key,
                    "messages": [],
                }
            elif request["method"] == "session.title":
                result = {"pending": False, "title": request["params"]["title"]}
            elif request["method"] == "session.history":
                result = {"messages": []}
            elif request["method"] == "prompt.submit":
                result = {"accepted": True}
            else:
                result = {"deleted": request["params"]["session_id"]}
            await _reply(websocket, request, result)

    async with websockets.serve(gateway, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        store = MemoryCredentialStore()
        controller = HermesResidentSessionController(_Route(port), store)
        runtime = _runtime()

        sessions = await controller.list_sessions(runtime)
        repeated = await controller.list_sessions(runtime)
        assert sessions[0].id == repeated[0].id
        assert sessions[0].title == "Persistent work"
        assert sessions[0].message_count == 4

        created = await controller.create_session(runtime, title="New work", model=runtime.model)
        assert created.id != sessions[0].id
        assert created in await controller.list_sessions(runtime)
        chat = await controller.connect_chat(runtime, created.id)
        await chat.send({"type": "user", "content": "First message"})
        await chat.close()
        await controller.delete_session(runtime, sessions[0].id)

    token = (await store.get_value("resident", str(runtime.id), "hermes-dashboard"))[
        "session_token"
    ]
    assert paths and all(path == f"/api/ws?token={token}" for path in paths)
    assert all(request["jsonrpc"] == "2.0" for request in requests)
    create = next(request for request in requests if request["method"] == "session.create")
    assert create["params"] == {
        "title": "New work",
        "model": runtime.model,
        "source": "desktop",
        "close_on_disconnect": False,
    }
    title = next(request for request in requests if request["method"] == "session.title")
    assert title["params"] == {"session_id": "live-created", "title": "New work"}
    assert not any(
        request["method"] == "session.resume" and request["params"].get("session_id") == created_key
        for request in requests
    )
    delete = next(request for request in requests if request["method"] == "session.delete")
    assert delete["params"] == {"session_id": persistent_key}


@pytest.mark.asyncio
async def test_resume_chat_correlates_rpc_and_normalizes_only_live_session_events() -> None:
    persistent_key = "20260711_120000_abc123"
    live_id = "live-7"
    requests: list[dict] = []
    approval_payload = {
        "command": "rm -rf build",
        "description": "recursive deletion",
        "allow_permanent": True,
    }

    async def emit(websocket, event_type: str, payload: dict, session_id: str = live_id) -> None:
        await websocket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": event_type,
                        "session_id": session_id,
                        "payload": payload,
                    },
                }
            )
        )

    async def gateway(websocket) -> None:
        await emit(websocket, "gateway.ready", {}, session_id="")
        async for raw in websocket:
            request = json.loads(raw)
            requests.append(request)
            method = request["method"]
            if method == "session.list":
                result = {"sessions": [{"id": persistent_key}]}
            elif method == "session.resume":
                result = {
                    "session_id": live_id,
                    "resumed": persistent_key,
                    "messages": [
                        {"role": "user", "text": "Earlier"},
                        {"role": "tool", "text": "not a visible turn"},
                        {"role": "assistant", "text": "Still here"},
                    ],
                    "info": {"model": "hermes-model"},
                }
            elif method == "session.history":
                result = {
                    "count": 3,
                    "messages": [
                        {"role": "user", "text": "Earlier"},
                        {"role": "tool", "text": "not a visible turn"},
                        {"role": "assistant", "text": "Still here"},
                    ],
                }
            else:
                result = {"status": "ok"}
            await _reply(websocket, request, result)

            if method == "prompt.submit":
                await emit(websocket, "message.delta", {"text": "wrong"}, "other-live")
                await emit(websocket, "message.start", {})
                await emit(websocket, "message.delta", {"text": "Hello"})
                await emit(
                    websocket,
                    "tool.start",
                    {"tool_id": "tool-1", "name": "terminal", "context": "ls"},
                )
                await emit(
                    websocket,
                    "tool.complete",
                    {"tool_id": "tool-1", "name": "terminal", "result": "README.md"},
                )
                await emit(
                    websocket,
                    "clarify.request",
                    {"request_id": "clarify-1", "question": "Pick one", "choices": ["A", "B"]},
                )
                await emit(websocket, "approval.request", approval_payload)
                await emit(
                    websocket,
                    "message.complete",
                    {"text": "Hello", "status": "complete"},
                )

    async with websockets.serve(gateway, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        controller = HermesResidentSessionController(_Route(port), MemoryCredentialStore())
        runtime = _runtime()
        listed = await controller.list_sessions(runtime)
        chat = await controller.connect_chat(runtime, listed[0].id)

        assert await chat.receive() == {
            "type": "capabilities",
            "interrupt": True,
            "steer": True,
            "set_model": False,
            "set_thinking_tokens": False,
        }
        history = await chat.receive()
        assert [turn["content"] for turn in history["turns"]] == ["Earlier", "Still here"]

        await chat.send({"type": "user", "content": "Hi", "request_id": "request-1"})
        assert (await chat.receive())["type"] == "user_confirmed"
        assert (await chat.receive())["type"] == "assistant"
        assert (await chat.receive())["delta"]["text"] == "Hello"

        tool_start = await chat.receive()
        assert tool_start == {
            "type": "tool_start",
            "data": "terminal",
            "metadata": {"tool_name": "terminal", "tool_id": "tool-1", "input": "ls"},
        }
        tool_result = await chat.receive()
        assert tool_result["type"] == "tool_result"
        assert tool_result["data"] == "README.md"

        clarify = await chat.receive()
        assert clarify == {
            "type": "ask_user_question",
            "request_id": "clarify-1",
            "questions": [
                {
                    "header": "Clarification",
                    "question": "Pick one",
                    "options": [{"label": "A"}, {"label": "B"}],
                    "multiSelect": False,
                }
            ],
        }
        approval = await chat.receive()
        assert approval["type"] == "control_request"
        assert approval["tool"] == "terminal"
        assert approval["input"] == approval_payload
        assert (await chat.receive()) == {"type": "result", "result": "Hello"}

        await chat.send(
            {
                "type": "ask_user_answer",
                "request_id": "clarify-1",
                "answers": [{"answer": "B"}],
            }
        )
        assert await chat.receive() == {
            "type": "ask_user_resolved",
            "request_id": "clarify-1",
        }
        await chat.send(
            {
                "type": "permission_response",
                "request_id": approval["request_id"],
                "behavior": "allowForever",
            }
        )
        assert await chat.receive() == {
            "type": "permission_resolved",
            "request_id": approval["request_id"],
            "behavior": "allowForever",
            "auto_approved": False,
        }
        await chat.send({"type": "steer_active_turn", "content": "Focus on tests"})
        await chat.send({"type": "interrupt"})
        await chat.close()

    resume = next(request for request in requests if request["method"] == "session.resume")
    assert resume["params"]["session_id"] == persistent_key
    assert next(request for request in requests if request["method"] == "session.history")[
        "params"
    ] == {"session_id": live_id}
    assert next(request for request in requests if request["method"] == "prompt.submit")[
        "params"
    ] == {"session_id": live_id, "text": "Hi"}
    assert next(request for request in requests if request["method"] == "clarify.respond")[
        "params"
    ] == {"session_id": live_id, "request_id": "clarify-1", "answer": "B"}
    assert next(request for request in requests if request["method"] == "approval.respond")[
        "params"
    ] == {"session_id": live_id, "choice": "always"}
    assert next(request for request in requests if request["method"] == "session.steer")[
        "params"
    ] == {"session_id": live_id, "text": "Focus on tests"}
    assert next(request for request in requests if request["method"] == "session.interrupt")[
        "params"
    ] == {"session_id": live_id}


@pytest.mark.asyncio
async def test_rejects_responses_not_scoped_to_pending_live_requests() -> None:
    class _UnusedGateway:
        async def request(self, *_args, **_kwargs):
            raise AssertionError("unknown responses must not reach Hermes")

        async def close(self):
            pass

    chat = HermesChatConnection(_UnusedGateway(), "live", [], "model")
    with pytest.raises(HermesGatewayError, match="Unknown Hermes approval"):
        await chat.send({"type": "permission_response", "request_id": "other", "behavior": "allow"})
    with pytest.raises(HermesGatewayError, match="Unknown Hermes input"):
        await chat.send(
            {"type": "ask_user_answer", "request_id": "other", "answers": [{"answer": "x"}]}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("behavior", "choice"),
    [("allow", "once"), ("allowForever", "always"), ("deny", "deny")],
)
async def test_permission_behaviors_map_to_hermes_choices(behavior: str, choice: str) -> None:
    class _Gateway:
        def __init__(self) -> None:
            self.events = asyncio.Queue()
            self.requests: list[tuple[str, dict]] = []

        async def request(self, method: str, params: dict):
            self.requests.append((method, params))
            return {"resolved": 1}

        async def close(self):
            pass

    gateway = _Gateway()
    chat = HermesChatConnection(gateway, "live", [], "model")
    await chat.receive()
    await chat.receive()
    approval = chat._normalize_event(  # noqa: SLF001 - focused adapter contract
        "approval.request",
        {"command": "pwd", "description": "check", "allow_permanent": True},
    )
    assert approval is not None

    await chat.send(
        {
            "type": "permission_response",
            "request_id": approval["request_id"],
            "behavior": behavior,
        }
    )

    assert gateway.requests == [("approval.respond", {"session_id": "live", "choice": choice})]


@pytest.mark.asyncio
async def test_rpc_errors_preserve_hermes_code() -> None:
    async def gateway(websocket) -> None:
        request = json.loads(await websocket.recv())
        await websocket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": 5006, "message": "database unavailable"},
                }
            )
        )

    async with websockets.serve(gateway, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        controller = HermesResidentSessionController(_Route(port), MemoryCredentialStore())
        with pytest.raises(HermesGatewayError, match="database unavailable") as raised:
            await controller.list_sessions(_runtime())

    assert raised.value.code == 5006


@pytest.mark.asyncio
async def test_message_edge_cases_and_command_validation() -> None:
    class _Gateway:
        def __init__(self) -> None:
            self.events = asyncio.Queue()
            self.requests: list[tuple[str, dict]] = []

        async def request(self, method: str, params: dict):
            self.requests.append((method, params))
            return {"status": "ok"}

        async def close(self):
            pass

    gateway = _Gateway()
    chat = HermesChatConnection(gateway, "live", [], "model")
    await chat.receive()
    await chat.receive()

    assistant = chat._normalize_event("message.delta", {"text": "first"})  # noqa: SLF001
    assert assistant and assistant["type"] == "assistant"
    assert (await chat.receive())["delta"]["text"] == "first"
    assert chat._normalize_event("message.complete", {"status": "error"}) == {  # noqa: SLF001
        "type": "error",
        "error": "Hermes turn failed",
    }

    completion_only = HermesChatConnection(gateway, "live", [], "model")
    await completion_only.receive()
    await completion_only.receive()
    assistant = completion_only._normalize_event(  # noqa: SLF001
        "message.complete", {"text": "whole reply", "status": "complete"}
    )
    assert assistant and assistant["type"] == "assistant"
    assert (await completion_only.receive())["delta"]["text"] == "whole reply"
    assert await completion_only.receive() == {"type": "result", "result": "whole reply"}
    assert completion_only._normalize_event("clarify.request", {}) is None  # noqa: SLF001
    assert completion_only._normalize_event("unknown", {}) is None  # noqa: SLF001

    await chat.send(
        {
            "type": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aW1hZ2U=",
                    },
                },
            ],
        }
    )
    assert gateway.requests[-2] == (
        "image.attach_bytes",
        {
            "session_id": "live",
            "content_base64": "aW1hZ2U=",
            "filename": "attachment-2.png",
        },
    )
    assert gateway.requests[-1] == (
        "prompt.submit",
        {"session_id": "live", "text": "hello"},
    )
    with pytest.raises(HermesGatewayError, match="Unsupported shared chat command"):
        await chat.send({"type": "set_model"})

    approval = chat._normalize_event("approval.request", {"command": "pwd"})  # noqa: SLF001
    assert approval is not None
    with pytest.raises(HermesGatewayError, match="Unsupported permission behavior"):
        await chat.send(
            {
                "type": "permission_response",
                "request_id": approval["request_id"],
                "behavior": "later",
            }
        )


@pytest.mark.asyncio
async def test_missing_resident_route_fails_before_websocket_connect() -> None:
    class _MissingRoute:
        def resident_proxy_target(self, _runtime):
            return None

    controller = HermesResidentSessionController(_MissingRoute(), MemoryCredentialStore())
    assert controller.engine is ResidentEngine.HERMES
    with pytest.raises(HermesGatewayError, match="no dashboard service route"):
        await controller.list_sessions(_runtime())
