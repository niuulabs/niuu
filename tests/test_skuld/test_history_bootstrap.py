"""Hermetic WS boundary: no live service, provider, or real user input."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocket

from skuld import broker_api
from skuld.channels import WebSocketChannel
from skuld.conversation_models import ConversationTurn
from skuld.conversation_read import history_write, wait_history_quiet
from skuld.transports import TransportCapabilities
from tests.test_skuld.test_event_log import _broker


def make_broker(tmp_path, **kwargs):
    broker = _broker(tmp_path, **kwargs)
    broker._transport = AsyncMock()
    broker._transport.is_alive = True
    broker._transport.capabilities = TransportCapabilities()
    broker._report_session_start = AsyncMock()
    return broker


async def connect(broker, *, query=b"history=recent&history_protocol=2", on_send=None):
    sent = []

    async def send(message):
        if message.get("type") != "websocket.send":
            return
        frame = json.loads(message["text"])
        sent.append(frame)
        if on_send:
            await on_send(frame)

    receive = AsyncMock(
        side_effect=[{"type": "websocket.connect"}, {"type": "websocket.disconnect", "code": 1000}]
    )
    ws = WebSocket({"type": "websocket", "headers": [], "query_string": query}, receive, send)
    await broker.handle_websocket(ws)
    return sent


async def test_append_while_snapshot_send_is_awaiting_is_only_post_snapshot(tmp_path):
    broker = make_broker(tmp_path)
    broker._conversation_turns = [ConversationTurn(id="first", role="user", content="first")]

    async def append_during_send(frame):
        if frame["type"] == "conversation_history":
            assert [turn["id"] for turn in frame["turns"]] == ["first"]
            broker._conversation_turns.append(
                ConversationTurn(id="second", role="user", content="second")
            )
            await broker._emit_broker_frame(
                {"type": "user_confirmed", "id": "second", "content": "second"}
            )

    frames = await connect(broker, on_send=append_during_send)
    snapshot = next(i for i, frame in enumerate(frames) if frame["type"] == "conversation_history")
    live = [i for i, frame in enumerate(frames) if frame.get("id") == "second"]
    assert len(live) == 1 and live[0] > snapshot
    assert frames[snapshot]["history_protocol"] == 2
    assert frames[snapshot]["refresh_cursor"]
    assert broker._channels.count == 0


async def test_connection_waits_for_broadcast_before_reduction_race(tmp_path):
    broker = make_broker(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    @history_write
    async def mutate(self):
        await self._channels.broadcast({"type": "assistant", "content": "synthetic"})
        entered.set()
        await release.wait()
        self._conversation_turns.append(
            ConversationTurn(id="finished", role="assistant", content="synthetic")
        )

    write = asyncio.create_task(mutate(broker))
    await entered.wait()
    read = asyncio.create_task(connect(broker))
    await asyncio.sleep(0)
    assert not read.done() and broker._channels.count == 0
    release.set()
    await write
    frames = await read
    history = next(frame for frame in frames if frame["type"] == "conversation_history")
    assert [turn["id"] for turn in history["turns"]] == ["finished"]
    assert not any(frame["type"] == "assistant" for frame in frames)


async def test_history_timeout_does_not_block_writer_and_is_typed_gap(tmp_path):
    broker = make_broker(tmp_path, history_read_timeout_seconds=0.001)
    broker._history_writes_in_flight = 1
    broker._history_quiet = asyncio.Event()
    frames = await connect(broker)
    gap = next(frame for frame in frames if frame["type"] == "history_gap")
    assert gap["reason"] == "snapshot_race" and gap["recovery"] == "recent"
    assert not any(frame["type"] == "conversation_history" for frame in frames)
    assert broker._history_writes_in_flight == 1


async def test_sender_only_connect_never_materializes_history(tmp_path):
    broker = make_broker(tmp_path)
    broker._serialize_in_progress_turn = lambda: pytest.fail("sender requested history")
    frames = await connect(broker, query=b"history=recent&history_protocol=2&history_delivery=none")
    assert not any(
        frame["type"] in {"conversation_history", "history_gap", "error"} for frame in frames
    )


async def test_bootstrap_overflow_is_bounded_control_not_silent_partial_history():
    ws = AsyncMock()
    channel = WebSocketChannel(ws, history_protocol=2, max_frame_bytes=2048)
    for i in range(1000):
        await channel.send_event({"type": "assistant", "content": str(i) * 500})
    assert channel._bootstrap_bytes <= 2048
    await channel.finish_history_bootstrap()
    frames = [json.loads(call.args[0]) for call in ws.send_text.await_args_list]
    assert len(frames) == 1 and frames[0]["type"] == "history_gap"
    await channel.send_event({"type": "result", "subtype": "success"})
    assert json.loads(ws.send_text.await_args.args[0])["type"] == "result"


async def test_reader_cancellation_never_cancels_writer(tmp_path):
    broker = make_broker(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    @history_write
    async def writer(self):
        entered.set()
        await release.wait()

    task = asyncio.create_task(writer(broker))
    await entered.wait()
    read = asyncio.create_task(wait_history_quiet(broker))
    await asyncio.sleep(0)
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    assert not task.done()
    release.set()
    await task
    assert broker._history_writes_in_flight == 0


async def test_gateway_bounds_before_serialization_and_reports_complete_count(
    tmp_path, monkeypatch
):
    broker = make_broker(tmp_path)
    broker._conversation_turns = [
        ConversationTurn(id=str(i), role="assistant", content="x") for i in range(100)
    ]
    broker._conversation_turns[0].metadata = {"must_not_serialize": object()}
    monkeypatch.setattr(broker_api, "broker", broker)
    page = await broker_api.get_conversation_history(history_protocol=2, limit=3, max_bytes=8192)
    assert page["history_protocol"] == 2 and page["total_turns"] == 100
    assert page["window_offset"] == 97 and len(page["turns"]) == 3
    assert page["history_settled_tail_id"] == "99"


@pytest.mark.parametrize(
    "params,code",
    [
        ({"cursor": "bad"}, 400),
        ({"history_protocol": 2, "cursor": "bad"}, 400),
        ({"history_protocol": 2, "max_bytes": 1000}, 413),
        ({"history_protocol": 2, "limit": -1}, 400),
        ({"history_protocol": 2, "turn_id": "0"}, 400),
        ({"turn_id": "missing"}, 404),
    ],
)
async def test_gateway_reports_invalid_or_unavailable_page_explicitly(
    tmp_path, monkeypatch, params, code
):
    broker = make_broker(tmp_path)
    broker._conversation_turns = [ConversationTurn(id="0", role="assistant", content="x")]
    monkeypatch.setattr(broker_api, "broker", broker)
    with pytest.raises(HTTPException) as error:
        await broker_api.get_conversation_history(**params)
    assert error.value.status_code == code


async def test_gateway_cursor_conflict_and_busy_are_recoverable_not_empty_success(
    tmp_path, monkeypatch
):
    broker = make_broker(tmp_path, history_read_timeout_seconds=0.001)
    broker._conversation_turns = [
        ConversationTurn(id=str(i), role="assistant", content="x") for i in range(4)
    ]
    monkeypatch.setattr(broker_api, "broker", broker)
    initial = await broker_api.get_conversation_history(history_protocol=2, limit=2)
    broker._conversation_turns[0].id = "resegmented"
    with pytest.raises(HTTPException) as conflict:
        await broker_api.get_conversation_history(
            history_protocol=2, cursor=initial["older_cursor"]
        )
    assert conflict.value.status_code == 409
    assert conflict.value.detail["code"] == "history_cursor_invalid"
    broker._history_writes_in_flight = 1
    broker._history_quiet = asyncio.Event()
    with pytest.raises(HTTPException) as busy:
        await broker_api.get_conversation_history(history_protocol=2)
    assert busy.value.status_code == 503 and busy.value.headers["Retry-After"] == "1"


async def test_protocol2_live_overflow_is_typed_control_and_count_limit_is_enforced():
    ws = AsyncMock()
    channel = WebSocketChannel(
        ws, history_protocol=2, max_frame_bytes=4096, history_bootstrap_max_frames=2
    )
    for i in range(3):
        await channel.send_event({"type": "assistant", "content": str(i)})
    await channel.finish_history_bootstrap()
    assert json.loads(ws.send_text.await_args.args[0])["reason"] == "snapshot_race"
    await channel.send_event({"type": "assistant", "content": "x" * 100_000})
    assert json.loads(ws.send_text.await_args.args[0])["reason"] == "live_frame_too_large"
    with pytest.raises(ValueError):
        WebSocketChannel(ws, history_protocol=2)
