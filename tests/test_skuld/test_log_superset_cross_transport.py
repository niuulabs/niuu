"""Epic F / INV-1 (+ INV-2): systematic all-frame-kinds superset across every
unit-drivable transport.

The §5 capture-everything contract (FR-2): whatever a transport normalizes and
emits to the broker MUST be appended to the durable event-log buffer FIRST, then
broadcast — for *every* transport, not just the one the live tmux harness drives.
Epic A's ``test_log_superset.py`` proved this for broker-originated frames and the
first-connect handshake; this file proves the same invariant holds frame-for-frame
for the events that arrive *through each transport's own normalization*.

What makes the assertions genuine (non-tautological):

  * We wire each transport's emit callback to the REAL production sink,
    ``Broker._handle_cli_event`` (the exact ``transport.on_event(broker._handle_cli_event)``
    wiring from ``broker.startup`` / ``BrokerHarness``). No fold is re-run; no event
    is hand-fed straight into the log.
  * We drive a representative slice of §5 kinds (text delta, thinking delta,
    tool_use, tool_result, result, error, permission/control_request) through the
    transport's *own* raw-frame normalization — the same methods the transport's
    read loop calls per decoded frame, or, for the loop-coupled transports, the
    real read loop itself (driven by the shared transport fakes, imported, never
    forked).
  * We instrument ``_channels.broadcast`` exactly like
    ``test_log_superset._instrument_broadcast`` and, AT BROADCAST TIME, record
    whether the broadcast frame is ALREADY present (by object identity) in
    ``broker._event_log_buffer``. The independently-derived expectation is "the log
    already contains this frame when broadcast fires" — log-before-broadcast — which
    is the structural statement of INV-1, not a re-derivation of the same data.

The transport fakes are reused by IMPORT only:
  * codex   — ``FakeWebSocket`` + the ``_handle_server_message`` dispatch the recv
              loop calls per frame (from ``test_codex_ws_transport``).
  * opencode — the ``_handle_sse_event`` dispatch the SSE loop calls per event.
  * sdk     — ``_FakeClient`` / ``_ClientFactory`` driving the real
              ``start`` + ``send_message`` path (from ``transports/test_sdk``).
  * grok    — the ACP stdin/stdout queue fakes driving the real ``_read_loop`` via
              ``send_message`` (mirroring ``test_grok_transport``).
  * persistent-subprocess — ``_StubStream`` / ``_make_proc`` driving the real
              ``_read_stdout_loop`` via ``send_message`` (from
              ``test_persistent_subprocess``).

This file lives under ``tests/test_skuld/*`` so the autouse SKULD__/VOLUNDR__/
BIFROST__ env strip in ``tests/test_skuld/conftest.py`` already applies — no
in-file env scrubbing is needed.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk.types import TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock

from skuld.broker import Broker
from skuld.config import SkuldSettings

# --- Shared transport fakes, imported (never forked) ------------------------
from skuld.transports.codex_ws import CodexWebSocketTransport
from skuld.transports.grok import GrokACPTransport
from skuld.transports.opencode import OpenCodeHttpTransport
from skuld.transports.persistent_subprocess import PersistentSubprocessTransport
from skuld.transports.sdk import SDKTransport
from tests.test_skuld.test_codex_ws_transport import FakeWebSocket as CodexFakeWebSocket
from tests.test_skuld.test_persistent_subprocess import _make_proc
from tests.test_skuld.transports.test_sdk import (
    _assistant_message,
    _ClientFactory,
    _result_message,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Broker + instrumentation (mirrors test_log_superset._broker / _instrument_broadcast)
# ---------------------------------------------------------------------------


def _broker(tmp_path) -> Broker:
    """A real Broker whose event log buffers (volundr_api_url truthy, flush loop
    never started) and which has zero attached channels — so the only broadcasts
    are the ones we drive through the transport."""
    settings = SkuldSettings(
        session={"id": "s-xtransport", "workspace_dir": str(tmp_path)},
        volundr_api_url="http://volundr.test",
        event_log_enabled=True,
    )
    return Broker(settings=settings)


def _logged_payload_ids(b: Broker) -> set[int]:
    return {id(e["payload"]) for e in b._event_log_buffer}


def _instrument_broadcast(b: Broker) -> tuple[list[dict], list[bool]]:
    """Record every frame that reaches ``_channels.broadcast`` AND, at the moment of
    each broadcast, whether the SAME frame object is already in the durable buffer.

    ``already_logged[i] is True`` is the structural witness for INV-1: the log is a
    superset and the append happened BEFORE the broadcast for frame ``i``."""
    broadcast_log: list[dict] = []
    already_logged: list[bool] = []
    original = b._channels.broadcast

    async def _record(frame: dict) -> None:
        broadcast_log.append(frame)
        already_logged.append(id(frame) in _logged_payload_ids(b))
        await original(frame)

    b._channels.broadcast = _record  # type: ignore[method-assign]
    return broadcast_log, already_logged


async def _settle() -> None:
    """Drain any fire-and-forget broker tasks the driven frames may have scheduled,
    so a late broadcast cannot escape the post-drive assertions."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Per-transport drivers. Each builds the transport, wires the REAL broker sink
# via on_event, drives a representative §5 slice through the transport's own
# normalization, and returns the set of frame *kinds* it expects to have emitted.
# ---------------------------------------------------------------------------


async def _drive_codex(broker: Broker, tmp_path) -> set[str]:
    t = CodexWebSocketTransport(workspace_dir=str(tmp_path), model="o4-mini", codex_port=19991)
    t.on_event(broker._handle_cli_event)
    t._ws = CodexFakeWebSocket()

    # turn/started -> assistant (new streaming message)
    await t._handle_server_message(
        {
            "method": "turn/started",
            "params": {
                "threadId": "t1",
                "turn": {"id": "turn-1", "items": [], "status": "running", "error": None},
            },
        }
    )
    # agentMessage delta -> content_block_delta (text_delta)
    await t._handle_server_message(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "t1", "turnId": "turn-1", "itemId": "i1", "delta": "Hello "},
        }
    )
    # reasoning delta -> content_block_delta (thinking_delta)
    await t._handle_server_message(
        {
            "method": "item/reasoning/textDelta",
            "params": {"threadId": "t1", "turnId": "turn-1", "delta": "thinking..."},
        }
    )
    # tool start -> assistant(tool_use) + content_block_start/delta
    await t._handle_item_started(
        {"type": "commandExecution", "id": "cmd-1", "command": "ls -la", "cwd": str(tmp_path)}
    )
    # tool completion -> content_block_start(tool_result) + stops
    await t._handle_item_completed(
        {"type": "commandExecution", "id": "cmd-1", "aggregatedOutput": "out", "exitCode": 0}
    )
    # permission request -> control_request
    await t._handle_server_request(
        {
            "id": 7,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "t1", "turnId": "turn-1", "itemId": "cmd-2", "command": "rm x"},
        }
    )
    # error -> error
    await t._handle_server_message(
        {"method": "error", "params": {"error": {"message": "rate limited"}, "willRetry": False}}
    )
    # turn/completed -> result
    await t._handle_server_message(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "t1",
                "turn": {"id": "turn-1", "items": [], "status": "completed", "error": None},
            },
        }
    )
    return {"assistant", "content_block_delta", "control_request", "error", "result"}


async def _drive_opencode(broker: Broker, tmp_path) -> set[str]:
    t = OpenCodeHttpTransport(workspace_dir=str(tmp_path), model="gpt-4o", opencode_port=19992)
    t.on_event(broker._handle_cli_event)

    await t._handle_sse_event({"type": "session.status", "properties": {"status": "analyzing"}})
    await t._handle_sse_event(
        {
            "type": "message.part.delta",
            "properties": {"sessionID": "s1", "partID": "p1", "field": "text", "delta": "Hi "},
        }
    )
    await t._handle_sse_event(
        {
            "type": "message.part.delta",
            "properties": {"sessionID": "s1", "partID": "p2", "field": "thinking", "delta": "hmm"},
        }
    )
    await t._handle_part_updated(
        {"type": "tool-invocation", "id": "tool-1", "toolName": "shell", "args": {"command": "ls"}},
        {},
    )
    await t._handle_sse_event(
        {
            "type": "question.asked",
            "properties": {"id": "perm-9", "tool": "shell", "question": "Run: rm x"},
        }
    )
    await t._handle_sse_event(
        {"type": "session.error", "properties": {"error": "model rate limited"}}
    )
    await t._handle_sse_event({"type": "session.idle", "properties": {"sessionID": "s1"}})
    return {"assistant", "content_block_delta", "control_request", "error", "result"}


async def _drive_sdk(broker: Broker, tmp_path, monkeypatch) -> set[str]:
    # Real start() + send_message() path: the assistant message carries text +
    # thinking + tool_use + tool_result blocks; a result message closes the turn.
    factory = _ClientFactory(
        [
            [
                _assistant_message(
                    TextBlock(text="world"),
                    ThinkingBlock(thinking="reason", signature="sig"),
                    ToolUseBlock(id="tool-1", name="read_file", input={"path": "README.md"}),
                    ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False),
                    session_id="sdk-session",
                ),
                _result_message(result="world"),
            ]
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    t = SDKTransport(workspace_dir=str(tmp_path))
    t.on_event(broker._handle_cli_event)
    await t.start()
    await t.send_message("hello")
    await t.stop()
    return {"assistant", "result"}


async def _drive_grok(broker: Broker, tmp_path, monkeypatch) -> set[str]:
    # Drive the REAL ACP read loop via the stdin/stdout queue fakes: handshake
    # answers are ready at start(); the prompt's streaming updates + result are
    # queued only after the turn is registered (id=3), exactly as a live agent.
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
    await queue.put(b'{"jsonrpc":"2.0","id":2,"result":{"sessionId":"s1"}}\n')

    async def fake_readline() -> bytes:
        return await queue.get()

    from unittest.mock import MagicMock

    mock_stdout = MagicMock()
    mock_stdout.readline = fake_readline
    mock_stdin = AsyncMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_process = MagicMock()
    mock_process.stdout = mock_stdout
    mock_process.stderr = None
    mock_process.stdin = mock_stdin
    mock_process.returncode = None
    mock_process.wait = AsyncMock(return_value=0)
    mock_process.communicate = AsyncMock(return_value=(b"", b""))

    t = GrokACPTransport(str(tmp_path), model="grok-build")
    t.on_event(broker._handle_cli_event)

    def _update(update: dict) -> bytes:
        return (
            json.dumps({"method": "session/update", "params": {"update": update}}) + "\n"
        ).encode()

    monkeypatch.setattr(
        "skuld.transports.grok.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_process),
    )
    await t.start()

    send_task = asyncio.create_task(t.send_message("do the thing"))
    for _ in range(100):
        if t._current_prompt_id is not None:
            break
        await asyncio.sleep(0.01)

    await queue.put(_update({"sessionUpdate": "agent_message_chunk", "content": {"text": "Hello"}}))
    await queue.put(
        _update({"sessionUpdate": "agent_thought_chunk", "content": {"text": "thinking"}})
    )
    await queue.put(
        _update(
            {
                "sessionUpdate": "tool_call",
                "tool": "search_replace",
                "arguments": {"file_path": "foo.py", "new_string": "bar"},
            }
        )
    )
    await queue.put(b'{"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn","text":"done"}}\n')
    await asyncio.wait_for(send_task, timeout=5)
    await t.stop()
    return {"assistant", "content_block_delta", "result"}


async def _drive_persistent_subprocess(broker: Broker, tmp_path, monkeypatch) -> set[str]:
    # Drive the REAL _read_stdout_loop via the _StubStream-backed proc + send_message.
    proc = _make_proc(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-x"}).encode()
            + b"\n",
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "ALPHA"},
                            {"type": "thinking", "thinking": "reason"},
                            {"type": "tool_use", "id": "tu-1", "name": "Read", "input": {}},
                        ],
                    },
                }
            ).encode()
            + b"\n",
            json.dumps({"type": "result", "subtype": "success", "result": "ALPHA"}).encode()
            + b"\n",
        ]
    )
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=proc))

    t = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")
    t.on_event(broker._handle_cli_event)
    await t.send_message("hello")
    await t.stop()
    return {"system", "assistant", "result"}


# A representative §5 kind that every transport in the matrix must surface.
_REQUIRED_COMMON_KIND = "assistant"


_TRANSPORTS = [
    ("codex", _drive_codex, False),
    ("opencode", _drive_opencode, False),
    ("sdk", _drive_sdk, True),
    ("grok", _drive_grok, True),
    ("persistent_subprocess", _drive_persistent_subprocess, True),
]


async def _run_driver(name, driver, needs_monkeypatch, broker, tmp_path, monkeypatch):
    if needs_monkeypatch:
        return await driver(broker, tmp_path, monkeypatch)
    return await driver(broker, tmp_path)


# ---------------------------------------------------------------------------
# INV-1 — the superset matrix
# ---------------------------------------------------------------------------


class TestCrossTransportSuperset:
    """INV-1 (FR-2 capture-everything): for EVERY unit-drivable transport, every frame
    the transport emits is appended to the durable log FIRST and then broadcast."""

    @pytest.mark.parametrize(("name", "driver", "needs_monkeypatch"), _TRANSPORTS)
    async def test_every_emitted_frame_logged_before_broadcast(
        self, name, driver, needs_monkeypatch, tmp_path, monkeypatch
    ):
        b = _broker(tmp_path)
        broadcast_log, already_logged = _instrument_broadcast(b)

        kinds = await _run_driver(name, driver, needs_monkeypatch, b, tmp_path, monkeypatch)
        await _settle()

        # The transport actually produced broadcasts (the driver wired real code).
        assert broadcast_log, f"{name}: expected the transport to broadcast frames"

        # INV-1 headline: at broadcast time, EVERY frame was already in the durable
        # buffer (append-before-broadcast). Independently derived: we check the log
        # membership at the instant of broadcast, not after the fact.
        logged_ids = _logged_payload_ids(b)
        for idx, frame in enumerate(broadcast_log):
            assert already_logged[idx] is True, (
                f"{name}: frame broadcast BEFORE it was logged: {frame}"
            )
            assert id(frame) in logged_ids, f"{name}: broadcast frame absent from log: {frame}"

        # Capture-everything by KIND: every kind the transport emitted is represented
        # in the durable log (not just present by identity — present by kind too).
        logged_kinds = {e["kind"] for e in b._event_log_buffer}
        broadcast_kinds = {f.get("type") for f in broadcast_log}
        for kind in broadcast_kinds:
            assert kind in logged_kinds, f"{name}: broadcast kind {kind!r} missing from log"

        # The driver's independently-declared expected kinds all actually landed in
        # the durable log — guards against a transport silently dropping a §5 kind.
        assert kinds <= logged_kinds, (
            f"{name}: expected kinds {kinds - logged_kinds} never reached the log"
        )
        assert _REQUIRED_COMMON_KIND in logged_kinds, (
            f"{name}: missing the common {_REQUIRED_COMMON_KIND!r} kind"
        )

    @pytest.mark.parametrize(("name", "driver", "needs_monkeypatch"), _TRANSPORTS)
    async def test_log_is_gapless_and_superset_of_broadcast(
        self, name, driver, needs_monkeypatch, tmp_path, monkeypatch
    ):
        """INV-2 corollary on the cross-transport path: with a generous buffer the
        durable seqs are contiguous 1..N (no overflow sentinel), and the log is a
        strict superset — it holds AT LEAST as many frames as were broadcast (broker
        funnels first-connect/system frames in too, but never fewer)."""
        b = _broker(tmp_path)
        broadcast_log, _ = _instrument_broadcast(b)

        await _run_driver(name, driver, needs_monkeypatch, b, tmp_path, monkeypatch)
        await _settle()

        seqs = [e["seq"] for e in b._event_log_buffer]
        assert seqs == list(range(1, len(seqs) + 1)), f"{name}: log seqs are not gapless: {seqs}"
        assert all(e["kind"] != "log_gap" for e in b._event_log_buffer), (
            f"{name}: unexpected overflow sentinel under a generous buffer"
        )
        assert len(b._event_log_buffer) >= len(broadcast_log), (
            f"{name}: log ({len(b._event_log_buffer)}) is not a superset of "
            f"broadcast ({len(broadcast_log)})"
        )


class TestProductionWiringIsExercised:
    """Guard against an accidentally tautological matrix: prove the sink under test is
    the real ``Broker._handle_cli_event`` (the production ``on_event`` target), so the
    superset property above is asserted against production code, not a stub."""

    async def test_on_event_target_is_handle_cli_event(self, tmp_path):
        b = _broker(tmp_path)
        t = OpenCodeHttpTransport(workspace_dir=str(tmp_path), opencode_port=19993)
        t.on_event(b._handle_cli_event)

        assert t.event_callback == b._handle_cli_event

        # And a single raw frame through the transport's normalization both logs and
        # broadcasts via that one production sink (sanity that the wiring is live).
        broadcast_log, already_logged = _instrument_broadcast(b)
        await t._handle_sse_event({"type": "session.error", "properties": {"error": "boom"}})

        assert [f.get("type") for f in broadcast_log] == ["error"]
        assert already_logged == [True]
        assert "error" in {e["kind"] for e in b._event_log_buffer}
