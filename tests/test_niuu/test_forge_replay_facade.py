"""The public Forge facade must carry replay WebSockets, including owner auth."""

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import respx
from fastapi import FastAPI, WebSocket
from httpx import Response
from starlette.datastructures import QueryParams
from starlette.websockets import WebSocketDisconnect

from niuu.adapters.inbound.ws_forge_replay import _relay, forward_replay
from niuu.adapters.outbound import guild_transport
from tests.test_niuu.test_guild_transport import _LEAF_CERT_DER, _LEAF_FINGERPRINT
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
    # A remote Guild instance never sees a client-supplied x-auth-* header,
    # even though _headers() below asserts one.
    assert "x-auth-user-id" not in seen["additional_headers"]
    assert "x-auth-tenant" not in seen["additional_headers"]
    assert seen["closed"]


@respx.mock
def test_remote_replay_owner_lookup_fails_closed_on_a_tls_pin_mismatch(monkeypatch):
    """The owner lookup (_find_session_owner -> _request_remote) is the
    first outbound leg and shares the same TLS-pinning factory as
    forward_replay, so a pin mismatch is already caught here — before
    forward_replay's own connect() is ever reached. See
    test_forward_replay_closes_on_its_own_pin_mismatch below for a direct,
    isolated test of forward_replay's own mismatch handling instead."""

    def _unexpected_connect(url, **kwargs):
        raise AssertionError("must not connect on a TLS pin mismatch")

    monkeypatch.setattr("niuu.adapters.inbound.ws_forge_replay.connect", _unexpected_connect)
    monkeypatch.setattr(
        "niuu.adapters.outbound.guild_transport.fetch_leaf_certificate_der",
        lambda *a, **k: b"not the pinned certificate",
    )
    pinned_fingerprint = "ab" * 32
    instance = _instance(
        "remote", base_url="https://owner", config={"tls_fingerprint": pinned_fingerprint}
    )
    with _client([instance]) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/api/v1/forge/sessions/s1/replay", headers=_headers()):
                pytest.fail("pin mismatch did not close the socket")
        assert closed.value.code == 1008


async def test_forward_replay_pins_a_matching_certificate_and_bridges(monkeypatch):
    """A direct, isolated call to forward_replay (bypassing the router-level
    owner lookup, which does its own separate pin check first) — proves the
    pinned-success branch is actually reached: a real SSLContext is resolved
    against a real loopback-verified certificate and passed into connect()."""
    monkeypatch.setattr(
        guild_transport, "fetch_leaf_certificate_der", lambda *a, **k: _LEAF_CERT_DER
    )
    captured: dict = {}

    def _connect(url, **kwargs):
        captured.update(url=url, **kwargs)
        raise OSError("stop after capturing connect() kwargs")

    monkeypatch.setattr("niuu.adapters.inbound.ws_forge_replay.connect", _connect)
    instance = _instance(
        "remote", base_url="https://owner", config={"tls_fingerprint": _LEAF_FINGERPRINT}
    )
    websocket = SimpleNamespace(query_params=QueryParams({}), close=AsyncMock(), accept=AsyncMock())

    await forward_replay(websocket, instance, "s1", headers={}, embedded_app=None)

    assert "ssl" in captured
    websocket.close.assert_awaited_once_with(code=1011)


async def test_forward_replay_closes_on_its_own_pin_mismatch(monkeypatch, caplog):
    """A direct, isolated call to forward_replay proving its own mismatch
    branch (except GuildTransportError: close(1011)) is reached, that
    connect() is never attempted on a mismatch, and — unlike the old silent
    close — that the refusal is logged with the instance and session it
    refused, so an operator can tell a policy refusal from a network blip."""
    monkeypatch.setattr(
        guild_transport,
        "fetch_leaf_certificate_der",
        lambda *a, **k: b"not the pinned certificate",
    )

    def _unexpected_connect(url, **kwargs):
        raise AssertionError("must not connect on a TLS pin mismatch")

    monkeypatch.setattr("niuu.adapters.inbound.ws_forge_replay.connect", _unexpected_connect)
    pinned_fingerprint = hashlib.sha256(b"the actual expected certificate").hexdigest()
    instance = _instance(
        "remote", base_url="https://owner", config={"tls_fingerprint": pinned_fingerprint}
    )
    websocket = SimpleNamespace(query_params=QueryParams({}), close=AsyncMock(), accept=AsyncMock())

    with caplog.at_level("WARNING", logger="niuu.adapters.inbound.ws_forge_replay"):
        await forward_replay(websocket, instance, "s1", headers={}, embedded_app=None)

    websocket.close.assert_awaited_once_with(code=1011)
    websocket.accept.assert_not_awaited()
    assert "remote" in caplog.text
    assert "s1" in caplog.text


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
