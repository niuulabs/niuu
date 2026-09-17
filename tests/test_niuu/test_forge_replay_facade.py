"""The public Forge facade must carry replay WebSockets, including owner auth."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import respx
from fastapi import FastAPI, WebSocket
from httpx import Response
from starlette.websockets import WebSocketDisconnect

from niuu.adapters.inbound.ws_forge_replay import _relay
from tests.test_niuu.test_rest_volundr import _client, _headers, _instance


def test_embedded_replay_preserves_frames_controls_identity_and_cursor():
    embedded = FastAPI()
    observed = {}

    @embedded.get("/api/v1/forge/sessions/{sid}")
    async def session(sid: str):
        return {"id": sid}

    @embedded.websocket("/api/v1/forge/sessions/{sid}/replay")
    async def replay(ws: WebSocket, sid: str):
        observed.update(sid=sid, after=ws.query_params["after"], token=ws.headers["authorization"])
        await ws.accept()
        await ws.send_json({"type": "assistant", "message": {"content": "captured"}})
        observed["control"] = await ws.receive_json()
        await ws.send_json({"type": "result"})
        await ws.close()

    instance = _instance("local", base_url="embedded://local", config={"transport": "embedded"})
    with _client([instance], embedded_forge_app=embedded) as client:
        with client.websocket_connect(
            "/api/v1/forge/sessions/s1/replay?after=17", headers=_headers()
        ) as ws:
            assert ws.receive_json()["type"] == "assistant"
            ws.send_json({"type": "set_internal_visibility", "visible": True})
            assert ws.receive_json()["type"] == "result"
    assert observed == {
        "sid": "s1",
        "after": "17",
        "token": "Bearer test-token",
        "control": {"type": "set_internal_visibility", "visible": True},
    }


def test_replay_cannot_reach_another_tenants_instance():
    instance = _instance("hidden", base_url="http://private", tenant_id="different-tenant")
    with _client([instance]) as client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect("/api/v1/forge/sessions/s1/replay", headers=_headers()):
                pytest.fail("hidden instance was reachable")
        assert denied.value.code == 1008


def test_backing_embedded_replay_keeps_its_own_authorization():
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions/{sid}")
    async def session(sid: str):
        return {"id": sid}

    @embedded.websocket("/api/v1/forge/sessions/{sid}/replay")
    async def replay(ws: WebSocket, sid: str):
        await ws.close(code=1008)

    instance = _instance("local", base_url="embedded://local", config={"transport": "embedded"})
    with _client([instance], embedded_forge_app=embedded) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/forge/sessions/s1/replay", headers=_headers()):
                pytest.fail("backing denial was bypassed")


@respx.mock
def test_remote_replay_uses_owner_url_and_forwards_auth_and_parameters(monkeypatch):
    respx.get("https://owner/api/v1/forge/sessions/s1").mock(
        return_value=Response(200, json={"id": "s1"})
    )
    seen = {}

    class Remote:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            seen["closed"] = True

        def __aiter__(self):
            return self.frames()

        async def frames(self):
            yield '{"type":"result"}'

        async def send(self, message):
            seen["control"] = message

    def connect(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Remote()

    monkeypatch.setattr("niuu.adapters.inbound.ws_forge_replay.connect", connect)
    with _client([_instance("remote", base_url="https://owner")]) as client:
        with client.websocket_connect(
            "/api/v1/forge/sessions/s1/replay?after=42&speed=4", headers=_headers()
        ) as ws:
            assert ws.receive_json() == {"type": "result"}
    assert seen["url"] == "wss://owner/api/v1/forge/sessions/s1/replay?after=42&speed=4"
    assert seen["additional_headers"]["authorization"] == "Bearer test-token"
    assert seen["closed"]


async def test_client_disconnect_cancels_a_sleeping_remote_replay():
    cancelled = asyncio.Event()

    class Remote:
        send = AsyncMock()

        def __aiter__(self):
            return self.frames()

        async def frames(self):
            try:
                await asyncio.Future()
                yield "unreachable"
            finally:
                cancelled.set()

    ws = SimpleNamespace(
        receive_text=AsyncMock(side_effect=WebSocketDisconnect),
        client_state=SimpleNamespace(name="DISCONNECTED"),
    )
    await asyncio.wait_for(_relay(ws, Remote()), timeout=1)
    assert cancelled.is_set()
