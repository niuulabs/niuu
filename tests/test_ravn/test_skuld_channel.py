"""Tests for SkuldChannel — WebSocket delivery channel."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from ravn.adapters.channels.skuld_channel import SkuldChannel
from ravn.domain.events import RavnEvent, RavnEventType

_SRC = "ravn-test"
_CID = "corr-1"
_SID = "sess-1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(broker_url: str = "ws://localhost:9000/ws/ravn/test") -> SkuldChannel:
    return SkuldChannel(
        broker_url=broker_url,
        session_id="test-session",
        reconnect_delay=0.0,
        max_reconnect_attempts=1,
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_serialise_response_event():
    ch = _make_channel()
    event = RavnEvent.response(_SRC, "Hello!", _CID, _SID)
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["type"] == "response"
    assert data["data"] == "Hello!"
    assert data["session_id"] == "test-session"
    assert "metadata" in data


def test_serialise_tool_start_event():
    ch = _make_channel()
    event = RavnEvent.tool_start(_SRC, "BashTool", {"command": "ls"}, _CID, _SID)
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["type"] == "tool_start"
    assert data["data"] == "BashTool"
    assert data["metadata"]["input"] == {"command": "ls"}


def test_serialise_error_event():
    ch = _make_channel()
    event = RavnEvent.error(_SRC, "boom", _CID, _SID)
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["type"] == "error"
    assert data["data"] == "boom"


def test_serialise_thought_event():
    ch = _make_channel()
    event = RavnEvent.thought(_SRC, "thinking...", _CID, _SID)
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["type"] == "thought"
    assert data["data"] == "thinking..."
    assert data["metadata"] == {}


def test_serialise_thinking_event():
    ch = _make_channel()
    event = RavnEvent.thinking(_SRC, "deep thought", _CID, _SID)
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["type"] == "thought"
    assert data["data"] == "deep thought"
    assert data["metadata"]["thinking"] is True


def test_serialise_tool_result_event():
    ch = _make_channel()
    event = RavnEvent.tool_result(_SRC, "echo", "output", _CID, _SID)
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["type"] == "tool_result"
    assert data["data"] == "output"
    assert data["metadata"]["tool_name"] == "echo"
    assert data["metadata"]["is_error"] is False


def test_serialise_tool_start_with_diff():
    ch = _make_channel()
    event = RavnEvent.tool_start(_SRC, "Edit", {"file": "a.py"}, _CID, _SID, diff="- old\n+ new")
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["metadata"]["diff"] == "- old\n+ new"


def test_serialise_ends_with_newline():
    ch = _make_channel()
    line = ch._serialise(RavnEvent.response(_SRC, "hi", _CID, _SID))
    assert line.endswith("\n")


# ---------------------------------------------------------------------------
# emit — happy path with mocked WebSocket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_sends_serialised_payload():
    ch = _make_channel()
    mock_ws = AsyncMock()
    mock_ws.closed = False
    ch._ws = mock_ws

    await ch.emit(RavnEvent.response(_SRC, "test response", _CID, _SID))

    mock_ws.send.assert_awaited_once()
    sent = mock_ws.send.call_args[0][0]
    data = json.loads(sent.strip())
    assert data["data"] == "test response"


@pytest.mark.asyncio
async def test_emit_buffers_event_on_failure():
    ch = _make_channel()

    # Make _send raise to trigger buffering.
    async def _fail(payload: str) -> None:
        raise RuntimeError("connection refused")

    ch._send = _fail  # type: ignore[method-assign]

    await ch.emit(RavnEvent.thought(_SRC, "thinking", _CID, _SID))

    assert len(ch._buffer) == 1
    assert ch._buffer[0].type == RavnEventType.THOUGHT


# ---------------------------------------------------------------------------
# flush_buffer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_buffer_sends_buffered_events():
    ch = _make_channel()
    ch._buffer = [RavnEvent.response(_SRC, "buffered", _CID, _SID)]

    mock_ws = AsyncMock()
    mock_ws.closed = False
    ch._ws = mock_ws

    await ch.flush_buffer()

    assert ch._buffer == []
    mock_ws.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_flush_buffer_noop_when_empty():
    ch = _make_channel()
    mock_ws = AsyncMock()
    ch._ws = mock_ws

    await ch.flush_buffer()

    mock_ws.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_closes_websocket():
    ch = _make_channel()
    mock_ws = AsyncMock()
    ch._ws = mock_ws

    await ch.disconnect()

    mock_ws.close.assert_awaited_once()
    assert ch._ws is None


@pytest.mark.asyncio
async def test_disconnect_noop_when_not_connected():
    ch = _make_channel()
    assert ch._ws is None
    await ch.disconnect()  # Should not raise


# ---------------------------------------------------------------------------
# connect — exhausts retries gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_logs_and_gives_up_after_max_retries():
    ch = _make_channel()

    with patch("ravn.adapters.channels.skuld_channel.websockets.connect") as mock_conn:
        mock_conn.side_effect = OSError("refused")
        await ch.connect()

    # After exhausting retries, ws remains None.
    assert ch._ws is None


@pytest.mark.asyncio
async def test_connect_noop_when_already_connected():
    """Connect returns early if ws is already open (covers line 86)."""
    ch = _make_channel()
    mock_ws = AsyncMock()
    mock_ws.closed = False
    ch._ws = mock_ws

    with patch("ravn.adapters.channels.skuld_channel.websockets.connect") as mock_conn:
        await ch.connect()
        mock_conn.assert_not_called()


@pytest.mark.asyncio
async def test_connect_success_sets_ws():
    """Successful connect stores the ws object (covers lines 123-128)."""
    ch = _make_channel()
    mock_ws = AsyncMock()
    mock_ws.closed = False

    connect_coro = AsyncMock(return_value=mock_ws)
    with patch("ravn.adapters.channels.skuld_channel.websockets.connect", new=connect_coro):
        await ch.connect()

    assert ch._ws is mock_ws


@pytest.mark.asyncio
async def test_connect_retries_with_delay():
    """Retry path with delay is covered (covers line 138)."""
    ch = SkuldChannel(
        broker_url="ws://localhost:9000/ws/ravn/test",
        session_id="test-session",
        reconnect_delay=0.0,
        max_reconnect_attempts=2,
    )
    call_count = 0
    mock_ws = AsyncMock()
    mock_ws.closed = False

    async def connect_fn(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise OSError("temp failure")
        return mock_ws

    with patch("ravn.adapters.channels.skuld_channel.websockets.connect", side_effect=connect_fn):
        await ch.connect()

    assert call_count == 2
    assert ch._ws is mock_ws


@pytest.mark.asyncio
async def test_disconnect_exception_swallowed():
    """Exception in ws.close() is swallowed (covers lines 95-96)."""
    ch = _make_channel()
    mock_ws = AsyncMock()
    mock_ws.close.side_effect = RuntimeError("close failed")
    ch._ws = mock_ws

    await ch.disconnect()  # Must not raise
    assert ch._ws is None


@pytest.mark.asyncio
async def test_flush_buffer_exception_requeues_event():
    """Flush failure requeues the event (covers lines 110-112)."""
    ch = _make_channel()
    event = RavnEvent.response(_SRC, "buffered", _CID, _SID)
    ch._buffer = [event]

    async def _fail(payload: str) -> None:
        raise RuntimeError("send failed")

    ch._send = _fail  # type: ignore[method-assign]
    await ch.flush_buffer()

    # Event was re-buffered
    assert len(ch._buffer) == 1


@pytest.mark.asyncio
async def test_send_triggers_connect_when_no_ws():
    """_send() calls connect when no ws is present (covers line 149)."""
    ch = _make_channel()
    mock_ws = AsyncMock()
    mock_ws.closed = False
    ch._ws = None

    connect_called = False

    async def fake_connect() -> None:
        nonlocal connect_called
        connect_called = True
        ch._ws = mock_ws

    ch.connect = fake_connect  # type: ignore[method-assign]
    await ch._send("test payload")

    assert connect_called
    mock_ws.send.assert_awaited_once_with("test payload")


@pytest.mark.asyncio
async def test_send_raises_when_ws_still_none_after_connect():
    """_send() raises RuntimeError when ws is still None after connect (covers line 152)."""
    ch = _make_channel()

    async def fake_connect() -> None:
        pass  # ws remains None

    ch.connect = fake_connect  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="no WebSocket connection"):
        await ch._send("payload")


@pytest.mark.asyncio
async def test_send_reconnects_on_connection_closed():
    """ConnectionClosed triggers reconnect and re-send (covers lines 156-161)."""
    import websockets.exceptions

    ch = _make_channel()
    mock_ws = AsyncMock()
    mock_ws.closed = False

    send_count = 0

    async def send_fn(payload: str) -> None:
        nonlocal send_count
        send_count += 1
        if send_count == 1:
            raise websockets.exceptions.ConnectionClosed(None, None)

    mock_ws.send = send_fn
    ch._ws = mock_ws

    new_ws = AsyncMock()
    new_ws.closed = False
    sent_payloads: list[str] = []
    new_ws.send = AsyncMock(side_effect=lambda p: sent_payloads.append(p))

    connect_coro = AsyncMock(return_value=new_ws)
    with patch("ravn.adapters.channels.skuld_channel.websockets.connect", new=connect_coro):
        await ch._send("hello")

    # connect() sends a register frame first, then the payload is re-sent
    assert "hello" in sent_payloads
    assert any('"type": "register"' in p for p in sent_payloads)


def test_serialise_task_complete_event():
    """TASK_COMPLETE and other events use the default case (covers lines 192-194)."""
    from datetime import UTC, datetime

    from ravn.domain.events import RavnEvent, RavnEventType

    ch = _make_channel()
    event = RavnEvent(
        type=RavnEventType.TASK_COMPLETE,
        source=_SRC,
        payload={"success": True},
        correlation_id=_CID,
        session_id=_SID,
        timestamp=datetime.now(UTC),
        urgency=0.5,
    )
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["type"] == "task_complete"
    assert "success" in data["data"]  # str(payload) will contain "success"


# ---------------------------------------------------------------------------
# source / persona fields (NIU-602)
# ---------------------------------------------------------------------------


def test_serialise_includes_source_when_peer_id_provided():
    ch = SkuldChannel(
        broker_url="ws://localhost:9000/ws/ravn/p1",
        session_id="sess-1",
        peer_id="ravn-agent-1",
    )
    line = ch._serialise(RavnEvent.response(_SRC, "Hello", _CID, _SID))
    data = json.loads(line.strip())
    assert data["source"] == "ravn-agent-1"


def test_serialise_includes_persona_when_provided():
    ch = SkuldChannel(
        broker_url="ws://localhost:9000/ws/ravn/p1",
        session_id="sess-1",
        peer_id="ravn-agent-1",
        persona="Aria",
    )
    line = ch._serialise(RavnEvent.response(_SRC, "Hello", _CID, _SID))
    data = json.loads(line.strip())
    assert data["persona"] == "Aria"


def test_serialise_omits_source_when_peer_id_not_provided():
    ch = _make_channel()  # no peer_id
    line = ch._serialise(RavnEvent.response(_SRC, "Hello", _CID, _SID))
    data = json.loads(line.strip())
    assert "source" not in data


def test_serialise_omits_persona_when_not_provided():
    ch = _make_channel()  # no persona
    line = ch._serialise(RavnEvent.response(_SRC, "Hello", _CID, _SID))
    data = json.loads(line.strip())
    assert "persona" not in data


def test_serialise_with_peer_id_preserves_existing_fields():
    ch = SkuldChannel(
        broker_url="ws://localhost:9000/ws/ravn/p1",
        session_id="sess-2",
        peer_id="agent-x",
        persona="Ravn",
    )
    event = RavnEvent.tool_start(_SRC, "BashTool", {"command": "ls"}, _CID, _SID)
    line = ch._serialise(event)
    data = json.loads(line.strip())
    assert data["type"] == "tool_start"
    assert data["data"] == "BashTool"
    assert data["session_id"] == "sess-2"
    assert data["source"] == "agent-x"
    assert data["persona"] == "Ravn"
