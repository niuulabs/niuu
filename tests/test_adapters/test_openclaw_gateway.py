"""Contract tests for the native OpenClaw resident session adapter."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.openclaw_gateway import (
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
        await chat.send({"type": "user", "content": "Hi", "request_id": "request-1"})
        confirmed = await chat.receive()
        assert confirmed["type"] == "user_confirmed"
        assert confirmed["request_id"] == "request-1"
        assert (await chat.receive())["type"] == "assistant"
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
