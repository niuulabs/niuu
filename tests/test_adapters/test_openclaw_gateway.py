"""Contract tests for the native OpenClaw resident session adapter."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound import openclaw_gateway as openclaw_runtime
from volundr.adapters.outbound.openclaw_gateway import (
    OpenClawChatConnection,
    OpenClawGatewayError,
    OpenClawResidentSessionController,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentEngine,
    ResidentRuntime,
    ResidentSessionStatus,
)


class _OpenShellRoute:
    def __init__(self, port: int) -> None:
        self.port = port

    def resident_proxy_target(self, _runtime: ResidentRuntime) -> SessionProxyTarget:
        return SessionProxyTarget(
            service_url=f"ws://127.0.0.1:{self.port}",
            connect_host="127.0.0.1",
            connect_port=self.port,
        )

    async def approve_resident_device(self, *_args, **_kwargs) -> None:
        raise AssertionError("pre-paired test Gateway must not request approval")


def _runtime() -> ResidentRuntime:
    return ResidentRuntime(
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Nemo",
        model="openai/gpt-5.6",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.OPENCLAW,
        profile_id="nemoclaw-openshell",
    )


class _RetryHistoryGateway:
    def __init__(self) -> None:
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.history_requests = 0
        self.closed = False

    async def request(self, method: str, _params: dict) -> dict:
        if method == "chat.send":
            return {"ok": True}
        if method != "chat.history":
            raise AssertionError(f"unexpected Gateway method: {method}")
        self.history_requests += 1
        messages = [
            {
                "id": "old-assistant",
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "Old answer"}],
            }
        ]
        if self.history_requests >= 3:
            messages.extend(
                [
                    {
                        "id": "retry-error",
                        "role": "assistant",
                        "stopReason": "error",
                        "content": [{"type": "text", "text": "terminated"}],
                    },
                    {
                        "id": "retry-success",
                        "role": "assistant",
                        "stopReason": "stop",
                        "content": [{"type": "text", "text": "Recovered answer"}],
                    },
                ]
            )
        return {"messages": messages}

    async def close(self) -> None:
        self.closed = True


class _QueueGateway:
    def __init__(self) -> None:
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.requests: list[tuple[str, dict]] = []
        self.closed = False

    async def request(self, method: str, params: dict) -> dict:
        self.requests.append((method, params))
        if method == "chat.history":
            return {"messages": []}
        return {"ok": True}

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_openclaw_recovers_completed_native_retry_from_history() -> None:
    gateway = _RetryHistoryGateway()
    key = "agent:main:niuu-11111111-2222-4333-8444-555555555555"
    initial_history = await gateway.request("chat.history", {})
    chat = OpenClawChatConnection(
        gateway,  # type: ignore[arg-type]
        key,
        initial_history,
        "openai/gpt-5.6",
        retry_history_timeout_seconds=0.1,
        retry_history_poll_interval_seconds=0.001,
    )

    assert (await chat.receive())["type"] == "capabilities"
    assert (await chat.receive())["turns"][0]["content"] == "Old answer"
    await chat.send({"type": "user", "content": "New question", "request_id": "request-1"})
    assert (await chat.receive())["type"] == "user_confirmed"
    await gateway.events.put(
        {
            "type": "event",
            "event": "chat",
            "payload": {
                "sessionKey": key,
                "runId": "run-1",
                "state": "error",
                "errorMessage": "terminated",
            },
        }
    )

    assert (await chat.receive())["type"] == "assistant"
    assert (await chat.receive())["delta"]["text"] == "Recovered answer"
    assert (await chat.receive())["type"] == "result"
    await chat.close()

    assert gateway.history_requests == 3
    assert gateway.closed is True


@pytest.mark.asyncio
async def test_openclaw_sessions_and_shared_chat_use_native_gateway_contract() -> None:
    resident_session_id = UUID("11111111-2222-4333-8444-555555555555")
    methods: list[str] = []
    requests: list[dict] = []
    connect_params: dict = {}

    async def gateway(websocket) -> None:
        await websocket.send(
            json.dumps({"type": "event", "event": "connect.challenge", "payload": {"nonce": "n-1"}})
        )
        connect = json.loads(await websocket.recv())
        connect_params.update(connect["params"])
        device = connect["params"]["device"]
        token = connect["params"]["auth"]["token"]
        signed = "|".join(
            (
                "v3",
                device["id"],
                "gateway-client",
                "backend",
                "operator",
                "operator.read,operator.write,operator.admin",
                str(device["signedAt"]),
                token,
                "n-1",
                "linux",
                "server",
            )
        )
        Ed25519PublicKey.from_public_bytes(
            base64.urlsafe_b64decode(device["publicKey"] + "==")
        ).verify(base64.urlsafe_b64decode(device["signature"] + "=="), signed.encode())
        await websocket.send(
            json.dumps(
                {
                    "type": "res",
                    "id": connect["id"],
                    "ok": True,
                    "payload": {
                        "protocol": 4,
                        "auth": {
                            "deviceToken": "paired-device-token",
                            "role": "operator",
                            "scopes": ["operator.read", "operator.write", "operator.admin"],
                        },
                    },
                }
            )
        )
        async for raw in websocket:
            request = json.loads(raw)
            requests.append(request)
            method = request["method"]
            methods.append(method)
            if method == "agents.list":
                payload = {"agents": [{"id": "main", "name": "NemoClaw"}]}
            elif method == "sessions.list":
                payload = {
                    "sessions": [
                        {
                            "key": f"agent:main:niuu-{resident_session_id}",
                            "label": "Persistent work",
                            "model": "openai/gpt-5.6",
                            "updatedAt": int(datetime.now(UTC).timestamp() * 1000),
                            "hasActiveRun": False,
                            "totalTokens": 42,
                            "estimatedCostUsd": 0.12,
                        },
                        {"key": "agent:main:unmanaged", "label": "Not a Niuu session"},
                    ]
                }
            elif method == "chat.history":
                payload = {
                    "messages": [
                        {
                            "id": "old-user",
                            "role": "user",
                            "content": [{"type": "text", "text": "Earlier"}],
                        },
                        {
                            "id": "old-assistant",
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Still here"}],
                        },
                    ]
                }
            else:
                payload = {"ok": True}
            await websocket.send(
                json.dumps(
                    {
                        "type": "res",
                        "id": request["id"],
                        "ok": True,
                        "payload": payload,
                    }
                )
            )
            if method == "chat.send":
                key = request["params"]["sessionKey"]
                await websocket.send(
                    json.dumps(
                        {
                            "type": "event",
                            "event": "chat",
                            "payload": {
                                "runId": "run-1",
                                "sessionKey": key,
                                "seq": 1,
                                "state": "delta",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "Hello"}],
                                },
                            },
                        }
                    )
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "event",
                            "event": "chat",
                            "payload": {
                                "runId": "run-1",
                                "sessionKey": key,
                                "seq": 2,
                                "state": "final",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "Hello"}],
                                },
                            },
                        }
                    )
                )

    async with websockets.serve(gateway, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        store = MemoryCredentialStore()
        controller = OpenClawResidentSessionController(_OpenShellRoute(port), store)
        runtime = _runtime()

        sessions = await controller.list_sessions(runtime)
        assert [(session.id, session.status) for session in sessions] == [
            (resident_session_id, ResidentSessionStatus.IDLE)
        ]
        assert sessions[0].tokens_used == 42
        assert sessions[0].chat_endpoint.endswith(f"/{resident_session_id}/session")

        created = await controller.create_session(runtime, title="New work", model=runtime.model)
        assert created.resident_id == runtime.id

        chat = await controller.connect_chat(runtime, resident_session_id)
        assert await chat.receive() == {
            "type": "capabilities",
            "interrupt": True,
            "set_model": False,
            "set_thinking_tokens": False,
        }
        history = await chat.receive()
        assert [turn["content"] for turn in history["turns"]] == ["Earlier", "Still here"]
        assert "participant_meta" not in history["turns"][0]
        assert history["turns"][1]["participant_meta"] == {
            "peer_id": "openclaw-primary",
            "persona": "NemoClaw",
            "display_name": "NemoClaw",
            "participant_type": "resident",
            "status": "idle",
        }
        await chat.send({"type": "user", "content": "Hi", "request_id": "request-1"})
        confirmed = await chat.receive()
        assert confirmed["type"] == "user_confirmed"
        assert confirmed["request_id"] == "request-1"
        assistant = await chat.receive()
        assert assistant["type"] == "assistant"
        assert assistant["participant"] == history["turns"][1]["participant_meta"]
        delta = await chat.receive()
        assert delta["delta"]["text"] == "Hello"
        assert (await chat.receive())["type"] == "result"
        await chat.send({"type": "interrupt"})
        await chat.close()

    assert connect_params["minProtocol"] == 3
    assert connect_params["maxProtocol"] == 4
    assert "sessions.list" in methods
    assert "agents.list" in methods
    assert "sessions.patch" in methods
    assert "chat.history" in methods
    assert "chat.send" in methods
    assert "chat.abort" in methods
    patch_request = next(request for request in requests if request["method"] == "sessions.patch")
    assert patch_request["params"]["label"] == "New work"
    assert patch_request["params"]["model"] == runtime.model
    assert patch_request["params"]["key"].startswith("agent:main:niuu-")
    history_request = next(request for request in requests if request["method"] == "chat.history")
    assert history_request["params"] == {
        "sessionKey": f"agent:main:niuu-{resident_session_id}",
        "limit": 1000,
    }
    credential = await store.get_value("resident", str(runtime.id), "openclaw-gateway")
    assert credential is not None
    assert credential["device_token"] == "paired-device-token"


def test_openclaw_history_helpers_filter_and_normalize_gateway_payloads() -> None:
    session_id = uuid4()
    key = f"agent:main:niuu-{session_id}"

    assert openclaw_runtime._session_uuid(key) == session_id
    assert openclaw_runtime._session_uuid("agent:main:other") is None
    assert openclaw_runtime._session_uuid("agent:main:niuu-invalid") is None
    assert openclaw_runtime._session_key(session_id) == key
    assert openclaw_runtime._message_text("invalid") == ""
    assert openclaw_runtime._message_text({"content": "plain"}) == "plain"
    assert openclaw_runtime._message_text({"content": {}}) == ""
    assert (
        openclaw_runtime._message_text(
            {
                "content": [
                    {"type": "text", "text": "one"},
                    {"type": "output_text", "text": " two"},
                    {"type": "image", "text": "ignored"},
                    "invalid",
                ]
            }
        )
        == "one two"
    )
    assert openclaw_runtime._history_messages(None) == []
    assert openclaw_runtime._history_messages({"messages": {}}) == []

    turns = openclaw_runtime._history_turns(
        {
            "messages": [
                {"role": "system", "content": "ignored"},
                {
                    "id": "user-1",
                    "role": "user",
                    "content": "hello",
                    "timestamp": 1_720_000_000_000,
                },
                {"role": "assistant", "content": "hi"},
                "invalid",
            ]
        }
    )
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[0]["id"] == "user-1"
    assert "participant_meta" not in turns[0]
    assert turns[1]["participant_meta"]["peer_id"] == "openclaw-primary"


@pytest.mark.asyncio
async def test_openclaw_chat_supports_attachments_and_final_only_responses() -> None:
    gateway = _QueueGateway()
    key = f"agent:main:niuu-{uuid4()}"
    chat = OpenClawChatConnection(
        gateway,  # type: ignore[arg-type]
        key,
        {},
        "openai/gpt-5.6",
    )
    await chat.receive()
    await chat.receive()

    with pytest.raises(OpenClawGatewayError, match="Unsupported"):
        await chat.send({"type": "control"})
    await chat.send(
        {
            "type": "user",
            "request_id": "request-1",
            "content": [
                {"type": "text", "text": "inspect "},
                "invalid",
                {"type": "image", "source": "invalid"},
                {
                    "type": "image",
                    "source": {
                        "media_type": "image/jpeg",
                        "data": "base64-data",
                    },
                },
            ],
        }
    )
    send_request = next(params for method, params in gateway.requests if method == "chat.send")
    assert send_request["message"] == "inspect "
    assert send_request["attachments"] == [
        {
            "mimeType": "image/jpeg",
            "fileName": "attachment-4",
            "content": "base64-data",
        }
    ]
    assert (await chat.receive())["type"] == "user_confirmed"

    await gateway.events.put(
        {"type": "event", "event": "chat", "payload": {"sessionKey": "foreign"}}
    )
    await gateway.events.put(
        {
            "type": "event",
            "event": "chat",
            "payload": {
                "sessionKey": key,
                "runId": "run-final",
                "state": "final",
                "message": {"content": "Final answer"},
            },
        }
    )
    assert (await chat.receive())["type"] == "assistant"
    assert (await chat.receive())["delta"]["text"] == "Final answer"
    assert (await chat.receive())["type"] == "result"

    await gateway.events.put(
        {
            "type": "event",
            "event": "chat",
            "payload": {
                "sessionKey": key,
                "runId": "run-aborted",
                "state": "aborted",
            },
        }
    )
    assert await chat.receive() == {"type": "error", "error": "aborted"}
    await chat.close()
    assert gateway.closed


@pytest.mark.asyncio
async def test_openclaw_chat_reports_closed_connection_and_retry_timeout() -> None:
    gateway = _QueueGateway()
    key = f"agent:main:niuu-{uuid4()}"
    chat = OpenClawChatConnection(
        gateway,  # type: ignore[arg-type]
        key,
        {},
        "openai/gpt-5.6",
        retry_history_timeout_seconds=0,
    )
    await chat.receive()
    await chat.receive()

    with pytest.raises(OpenClawGatewayError, match="history timeout"):
        await chat._await_native_retry()

    await gateway.events.put({"type": "event", "event": "connection.closed", "payload": {}})
    with pytest.raises(OpenClawGatewayError, match="connection closed"):
        await chat.receive()
