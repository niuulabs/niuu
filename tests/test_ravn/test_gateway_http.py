"""Tests for the HTTP gateway adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ravn.adapters.channels.gateway import RavnGateway
from ravn.adapters.channels.gateway_http import ChatRequest, HttpGateway
from ravn.config import HttpChannelConfig
from ravn.domain.events import RavnEvent

_SRC = "ravn-test"
_CID = "corr-1"
_SID = "sess-1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_config(host: str = "127.0.0.1", port: int = 7477) -> HttpChannelConfig:
    return HttpChannelConfig(enabled=True, host=host, port=port)


def _make_gateway_mock(response: str = "agent reply") -> RavnGateway:
    """Return a mock RavnGateway that returns a canned response."""
    gw = MagicMock(spec=RavnGateway)
    gw.session_ids.return_value = ["http:default"]
    gw.get_status.return_value = {"session_count": 1, "active_sessions": ["http:default"]}

    async def handle_stream(session_id: str, message: str) -> AsyncIterator[RavnEvent]:
        yield RavnEvent.thought(_SRC, "thinking...", _CID, _SID)
        yield RavnEvent.response(_SRC, response, _CID, _SID)

    gw.handle_message_stream = handle_stream

    q: asyncio.Queue = asyncio.Queue()
    gw.subscribe.return_value = q
    gw.unsubscribe = MagicMock()
    return gw


def _make_http_gateway(gw: RavnGateway | None = None) -> HttpGateway:
    if gw is None:
        gw = _make_gateway_mock()
    return HttpGateway(_make_http_config(), gw)


def _get_test_client(gateway: HttpGateway | None = None) -> TestClient:
    gw_obj = _make_http_gateway(gateway)
    return TestClient(gw_obj.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


def test_status_endpoint_returns_session_info():
    client = _get_test_client()
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "session_count" in data
    assert "active_sessions" in data
    assert data["session_count"] == 1


def test_status_endpoint_no_sessions():
    gw = MagicMock(spec=RavnGateway)
    gw.session_ids.return_value = []
    gw.get_status.return_value = {"session_count": 0, "active_sessions": []}
    ht = HttpGateway(_make_http_config(), gw)
    client = TestClient(ht.app)
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json()["session_count"] == 0


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


def test_chat_endpoint_returns_sse_stream():
    client = _get_test_client()
    resp = client.post(
        "/chat",
        json={"message": "hello", "session_id": "http:default"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


def test_chat_endpoint_streams_events():
    client = _get_test_client()
    resp = client.post(
        "/chat",
        json={"message": "hello"},
    )
    body = resp.text
    # Should contain SSE data lines
    assert "data: " in body
    lines = [ln for ln in body.splitlines() if ln.startswith("data: ")]
    assert len(lines) >= 1

    # Each line must be valid JSON with the new payload format
    for line in lines:
        payload = json.loads(line[len("data: ") :])
        assert "type" in payload
        assert "payload" in payload


def test_chat_endpoint_includes_thought_and_response():
    client = _get_test_client()
    resp = client.post("/chat", json={"message": "hi"})

    lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
    types = [json.loads(ln[len("data: ") :])["type"] for ln in lines]

    assert "thought" in types
    assert "response" in types


def test_chat_endpoint_default_session_id():
    """Omitting session_id uses 'http:default'."""
    gw = MagicMock(spec=RavnGateway)
    gw.session_ids.return_value = []

    async def handle_stream(session_id: str, message: str) -> AsyncIterator[RavnEvent]:
        assert session_id == "http:default"
        yield RavnEvent.response(_SRC, "ok", _CID, _SID)

    gw.handle_message_stream = handle_stream

    ht = HttpGateway(_make_http_config(), gw)
    client = TestClient(ht.app)
    resp = client.post("/chat", json={"message": "hi"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /events
# ---------------------------------------------------------------------------


def test_events_endpoint_returns_sse():
    """GET /events returns text/event-stream with correct SSE framing."""
    gw = MagicMock(spec=RavnGateway)

    ht = HttpGateway(_make_http_config(), gw)

    # Patch _broadcast_stream to yield one event and return so the
    # TestClient receives a finite response instead of blocking forever.
    async def finite_broadcast():
        payload = json.dumps(
            {
                "type": "response",
                "payload": {"text": "hi"},
                "source": _SRC,
                "session_id": _SID,
                "timestamp": "2026-04-06T00:00:00+00:00",
            }
        )
        yield f"data: {payload}\n\n"

    ht._broadcast_stream = finite_broadcast

    client = TestClient(ht.app)
    resp = client.get("/events")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "data: " in resp.text


# ---------------------------------------------------------------------------
# HttpGateway.app property
# ---------------------------------------------------------------------------


def test_app_property_returns_fastapi_app():
    from fastapi import FastAPI

    ht = _make_http_gateway()
    assert isinstance(ht.app, FastAPI)


# ---------------------------------------------------------------------------
# ChatRequest schema
# ---------------------------------------------------------------------------


def test_chat_request_default_session_id():
    req = ChatRequest(message="hi")
    assert req.session_id == "http:default"


def test_chat_request_custom_session_id():
    req = ChatRequest(message="hi", session_id="my-session")
    assert req.session_id == "my-session"


# ---------------------------------------------------------------------------
# SSE format helpers
# ---------------------------------------------------------------------------


def test_sse_payload_contains_source_and_timestamp():
    """Verify source and timestamp fields are present in streamed events."""
    client = _get_test_client()
    resp = client.post("/chat", json={"message": "hi"})

    lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
    for line in lines:
        payload = json.loads(line[len("data: ") :])
        assert "source" in payload
        assert "timestamp" in payload


# ---------------------------------------------------------------------------
# _broadcast_stream — real queue-based implementation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_stream_yields_events_from_queue():
    """_broadcast_stream reads real events from the subscribe queue."""
    gw = MagicMock(spec=RavnGateway)
    q: asyncio.Queue = asyncio.Queue()
    gw.subscribe.return_value = q
    gw.unsubscribe = MagicMock()

    ht = HttpGateway(_make_http_config(), gw)

    # Push one event then the sentinel None.
    event = RavnEvent.response(_SRC, "broadcast msg", _CID, _SID)
    await q.put(event)
    await q.put(None)

    lines = []
    async for chunk in ht._broadcast_stream():
        lines.append(chunk)

    gw.unsubscribe.assert_called_once_with(q)
    assert len(lines) == 1
    payload = json.loads(lines[0][len("data: ") :])
    assert payload["payload"]["text"] == "broadcast msg"


@pytest.mark.asyncio
async def test_broadcast_stream_unsubscribes_on_exit():
    """unsubscribe is called even if the loop is exited via sentinel."""
    gw = MagicMock(spec=RavnGateway)
    q: asyncio.Queue = asyncio.Queue()
    gw.subscribe.return_value = q
    gw.unsubscribe = MagicMock()

    ht = HttpGateway(_make_http_config(), gw)
    await q.put(None)

    async for _ in ht._broadcast_stream():
        pass

    gw.unsubscribe.assert_called_once_with(q)


# ---------------------------------------------------------------------------
# run() — uvicorn lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_starts_and_cancels_cleanly(monkeypatch):
    """run() wires up uvicorn and re-raises CancelledError."""
    import uvicorn

    served = []

    class FakeServer:
        def __init__(self, config):
            pass

        async def serve(self):
            served.append(True)
            raise asyncio.CancelledError()

    monkeypatch.setattr(uvicorn, "Server", FakeServer)

    gw = MagicMock(spec=RavnGateway)
    ht = HttpGateway(_make_http_config(), gw)

    with pytest.raises(asyncio.CancelledError):
        await ht.run()

    assert served


# ---------------------------------------------------------------------------
# WS /ws — WebSocket chat endpoint
# ---------------------------------------------------------------------------


def test_ws_endpoint_streams_cli_format_events():
    """WebSocket /ws returns CLI stream-json events translated from RavnEvents."""
    ht = _make_http_gateway()
    client = TestClient(ht.app)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "user", "content": "hello"}))

        events = []
        # Read events until we get a result (end of turn).
        while True:
            raw = ws.receive_text()
            evt = json.loads(raw)
            events.append(evt)
            if evt["type"] in ("result", "error"):
                break

    types = [e["type"] for e in events]
    assert "assistant" in types
    assert "result" in types


def test_ws_endpoint_thought_produces_text_delta():
    """THOUGHT events arrive as content_block_delta with text_delta type."""
    ht = _make_http_gateway()
    client = TestClient(ht.app)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "user", "content": "hi"}))

        events = []
        while True:
            raw = ws.receive_text()
            evt = json.loads(raw)
            events.append(evt)
            if evt["type"] in ("result", "error"):
                break

    deltas = [e for e in events if e["type"] == "content_block_delta"]
    assert any(d["delta"]["type"] == "text_delta" for d in deltas)


def test_ws_endpoint_tool_produces_tool_use_block():
    """TOOL_START events produce content_block_start with type tool_use."""
    gw = MagicMock(spec=RavnGateway)
    gw.session_ids.return_value = []

    async def handle_stream(session_id: str, message: str) -> AsyncIterator[RavnEvent]:
        yield RavnEvent.tool_start(_SRC, "Bash", {"command": "ls"}, _CID, _SID)
        yield RavnEvent.tool_result(_SRC, "Bash", "file.py", _CID, _SID)
        yield RavnEvent.response(_SRC, "Done", _CID, _SID)

    gw.handle_message_stream = handle_stream

    ht = HttpGateway(_make_http_config(), gw)
    client = TestClient(ht.app)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "user", "content": "go"}))

        events = []
        while True:
            raw = ws.receive_text()
            evt = json.loads(raw)
            events.append(evt)
            if evt["type"] in ("result", "error"):
                break

    tool_starts = [
        e
        for e in events
        if e["type"] == "content_block_start"
        and e.get("content_block", {}).get("type") == "tool_use"
    ]
    assert len(tool_starts) == 1
    assert tool_starts[0]["content_block"]["name"] == "Bash"


def test_ws_endpoint_ignores_non_user_messages():
    """Non-user messages should not trigger agent turns."""
    ht = _make_http_gateway()
    client = TestClient(ht.app)

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "interrupt"}))
        # Send a real message to verify the connection works
        ws.send_text(json.dumps({"type": "user", "content": "hi"}))

        events = []
        while True:
            raw = ws.receive_text()
            evt = json.loads(raw)
            events.append(evt)
            if evt["type"] in ("result", "error"):
                break

    # Should get events only from the user message
    assert any(e["type"] == "result" for e in events)
