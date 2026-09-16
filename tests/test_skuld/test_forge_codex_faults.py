"""Exercise Codex failures through its actual reader and RPC awaiters."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from tests.test_skuld.test_codex_ws_transport import _make_transport


class Wire:
    def __init__(self, frames=()):
        self.frames = list(frames)
        self.sent = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        frame = self.frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return frame

    async def send(self, data):
        self.sent.put_nowait(json.loads(data))

    async def close(self):
        pass


@pytest.mark.parametrize("ending", [[], [ConnectionError("lost socket")]])
async def test_reader_exit_closes_active_turn_once_and_releases_rpc(tmp_path, ending):
    transport = _make_transport(tmp_path)
    transport._ws = Wire(ending)
    transport._thread_id = "thread"
    transport._current_turn_id = "turn"
    transport._active_user_prompt = "working"
    transport._alive = True
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    request = asyncio.create_task(transport._send_rpc("turn/interrupt", {}))
    await asyncio.wait_for(transport._ws.sent.get(), 1)
    try:
        await transport._receive_loop()
        assert not transport.is_alive
        assert not transport.is_turn_active
        with pytest.raises(RuntimeError, match="closed"):
            await asyncio.wait_for(request, 1)
        await transport.stop()
        results = [e for e in events if e["type"] == "result"]
        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert results[0]["stop_reason"] == "error"
        assert transport._pending == {}
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("noise", ["null", "[]", "42", '"noise"', "{broken"])
async def test_protocol_noise_does_not_drop_following_valid_output(tmp_path, noise):
    transport = _make_transport(tmp_path)
    transport._ws = Wire(
        [noise, json.dumps({"method": "item/agentMessage/delta", "params": {"delta": "alive"}})]
    )
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    await transport._receive_loop()
    assert any(e.get("delta", {}).get("text") == "alive" for e in events)


async def test_rpc_send_failure_removes_pending_future(tmp_path):
    transport = _make_transport(tmp_path)
    transport._ws = Wire()
    transport._ws.send = AsyncMock(side_effect=ConnectionError("broken pipe"))
    with pytest.raises(ConnectionError):
        await transport._send_rpc("turn/start", {})
    assert transport._pending == {}


async def test_cancelled_rpc_removes_pending_future(tmp_path):
    transport = _make_transport(tmp_path)
    transport._ws = Wire()
    request = asyncio.create_task(transport._send_rpc("turn/start", {}))
    await asyncio.wait_for(transport._ws.sent.get(), 1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert transport._pending == {}


@pytest.mark.parametrize("status,reason", [("failed", "error"), ("interrupted", "cancelled")])
async def test_terminal_status_is_not_reported_as_success(tmp_path, status, reason):
    transport = _make_transport(tmp_path)
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    await transport._handle_server_message(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "t1", "status": status, "error": "provider detail"}},
        }
    )
    result = next(e for e in events if e["type"] == "result")
    assert result["stop_reason"] == reason
    assert result["is_error"] is (status == "failed")
    assert result["error"] == "provider detail"


async def test_concurrent_starts_share_one_ready_transport(tmp_path):
    transport = _make_transport(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def spawn():
        entered.set()
        await release.wait()

    async def handshake():
        transport._thread_id = "ready-thread"
        transport._alive = True

    transport._spawn_app_server = AsyncMock(side_effect=spawn)
    transport._connect_ws = AsyncMock()
    transport._handshake = AsyncMock(side_effect=handshake)
    first = asyncio.create_task(transport.start())
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(transport.start())
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    transport._spawn_app_server.assert_awaited_once()
    assert transport.session_id == "ready-thread"


async def test_compaction_rpc_response_can_be_read_while_recovery_is_running(tmp_path):
    transport = _make_transport(tmp_path)
    transport._thread_id = "thread"
    transport._current_turn_id = "turn"
    transport._active_user_prompt = "finish the task"

    class CompactionWire(Wire):
        async def __anext__(self):
            if self.frames:
                return self.frames.pop(0)
            # This response cannot be produced until the reader has dispatched
            # the error and the recovery has sent its RPC. No mocked _send_rpc.
            request = await asyncio.wait_for(self.sent.get(), 1)
            assert request["method"] == "thread/compact/start"
            self.frames.append(json.dumps({"method": "thread/status/changed", "params": {}}))
            return json.dumps({"id": request["id"], "result": {"turn": {"id": "compact"}}})

    wire = CompactionWire(
        [
            json.dumps(
                {
                    "method": "error",
                    "params": {"error": {"message": "context window exceeded"}},
                }
            )
        ]
    )
    transport._ws = wire
    reader = asyncio.create_task(transport._receive_loop())
    try:
        for _ in range(100):
            if transport._context_compaction_turn_id == "compact":
                break
            await asyncio.sleep(0.001)
        assert transport._context_compaction_turn_id == "compact"
        assert transport._pending == {}
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        await transport.stop()


async def test_compaction_start_failure_closes_the_turn(tmp_path):
    transport = _make_transport(tmp_path)
    transport._thread_id = "thread"
    transport._active_user_prompt = "task"
    transport._send_rpc = AsyncMock(side_effect=RuntimeError("compaction refused"))
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    await transport._recover_from_context_window_exceeded("context window exceeded")
    await transport._compaction_task
    assert not transport.is_turn_active
    results = [e for e in events if e["type"] == "result"]
    assert len(results) == 1
    assert results[0]["is_error"] is True
