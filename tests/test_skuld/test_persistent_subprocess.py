"""Tests for PersistentSubprocessTransport.

The transport spawns ``claude -p --input-format stream-json`` once per
session and reuses it across turns. Tests stub the asyncio subprocess so
we exercise the protocol logic (stdin write, stdout demultiplex, turn
completion via ``result`` events) without a real Claude CLI.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.transports.persistent_subprocess import PersistentSubprocessTransport


class _StubStream:
    """Async stream stub backed by a list of byte lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for line in lines:
            self._queue.put_nowait(line)

    def push(self, line: bytes) -> None:
        self._queue.put_nowait(line)

    def close(self) -> None:
        # Sentinel: empty bytes means EOF for readline.
        self._queue.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _StubStdin:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


def _make_proc(stdout_lines: list[bytes], pid: int = 1234) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = None
    proc.stdout = _StubStream(stdout_lines)
    proc.stderr = _StubStream([b""])
    proc.stdin = _StubStdin()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


def _result_line(text: str) -> bytes:
    return json.dumps({"type": "result", "subtype": "success", "result": text}).encode() + b"\n"


def _assistant_line(text: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            }
        ).encode()
        + b"\n"
    )


def _system_line(session_id: str) -> bytes:
    return (
        json.dumps({"type": "system", "subtype": "init", "session_id": session_id}).encode()
        + b"\n"
    )


@pytest.mark.asyncio
async def test_send_message_returns_when_result_event_arrives(tmp_path) -> None:
    """send_message blocks until the corresponding result event."""
    proc = _make_proc(
        [
            _system_line("sess-abc"),
            _assistant_line("ALPHA"),
            _result_line("ALPHA"),
        ]
    )
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.send_message("hello")

    assert b'"content": "hello"' in proc.stdin.buf or b'"content":"hello"' in proc.stdin.buf
    assert transport.session_id == "sess-abc"
    assert transport.last_result is not None
    assert transport.last_result.get("result") == "ALPHA"
    await transport.stop()


@pytest.mark.asyncio
async def test_two_turns_reuse_same_process(tmp_path) -> None:
    """Two send_message calls write to the same stdin without respawning."""
    proc = _make_proc(
        [
            _system_line("sess-multi"),
            _assistant_line("ALPHA"),
            _result_line("ALPHA"),
            # Second turn arrives later
        ]
    )
    spawn = AsyncMock(return_value=proc)
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    with patch("asyncio.create_subprocess_exec", spawn):
        await transport.send_message("first")
        # Now push the second turn's events
        proc.stdout.push(_assistant_line("BETA"))
        proc.stdout.push(_result_line("BETA"))
        await transport.send_message("second")

    assert spawn.await_count == 1, "should spawn Claude only once across turns"
    body = bytes(proc.stdin.buf)
    assert b'"content": "first"' in body or b'"content":"first"' in body
    assert b'"content": "second"' in body or b'"content":"second"' in body
    await transport.stop()


@pytest.mark.asyncio
async def test_events_fan_out_to_callback(tmp_path) -> None:
    """Assistant and other events get forwarded to the on_event callback."""
    proc = _make_proc(
        [
            _system_line("sess-cb"),
            _assistant_line("hi"),
            _result_line("hi"),
        ]
    )
    received: list[dict] = []

    async def cb(event: dict) -> None:
        received.append(event)

    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")
    transport.on_event(cb)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.send_message("hi")

    types = [e.get("type") for e in received]
    assert "system" in types
    assert "assistant" in types
    assert "result" in types
    await transport.stop()


@pytest.mark.asyncio
async def test_start_sends_initial_prompt(tmp_path) -> None:
    """start() with an initial_prompt writes it to stdin and waits for result."""
    proc = _make_proc(
        [
            _system_line("sess-init"),
            _assistant_line("ack"),
            _result_line("ack"),
        ]
    )
    transport = PersistentSubprocessTransport(
        str(tmp_path), initial_prompt="run setup"
    )

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.start()

    body = bytes(proc.stdin.buf)
    assert b"run setup" in body
    await transport.stop()


@pytest.mark.asyncio
async def test_start_without_initial_prompt_does_not_write(tmp_path) -> None:
    """start() with no initial_prompt only spawns; no stdin write."""
    proc = _make_proc([])
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.start()

    assert proc.stdin.buf == bytearray()
    await transport.stop()


@pytest.mark.asyncio
async def test_send_message_concurrency_is_serialized(tmp_path) -> None:
    """Two concurrent send_message calls run sequentially under the lock."""
    proc = _make_proc(
        [
            _system_line("sess-concurrent"),
            _result_line("first done"),
        ]
    )
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    async def send_second() -> None:
        # Tiny delay so the first call grabs the lock; then queue events
        # for the second turn after a moment.
        await asyncio.sleep(0.01)
        await transport.send_message("second")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        first = asyncio.create_task(transport.send_message("first"))
        second = asyncio.create_task(send_second())
        # Wait until first turn is awaiting the result event, then push it
        # plus the result event for the second turn.
        await asyncio.sleep(0.05)
        proc.stdout.push(_result_line("second done"))
        await asyncio.gather(first, second)

    # Both writes landed in order.
    body = bytes(proc.stdin.buf)
    first_idx = body.find(b'"first"')
    second_idx = body.find(b'"second"')
    assert 0 <= first_idx < second_idx, "first should be written before second"
    await transport.stop()


@pytest.mark.asyncio
async def test_stop_closes_stdin(tmp_path) -> None:
    proc = _make_proc([])
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.start()
        await transport.stop()

    assert proc.stdin.closed is True


@pytest.mark.asyncio
async def test_capabilities() -> None:
    transport = PersistentSubprocessTransport("/tmp", initial_prompt="")
    caps = transport.capabilities
    assert caps.session_resume is True
    assert caps.interrupt is False
    assert caps.cli_websocket is False
