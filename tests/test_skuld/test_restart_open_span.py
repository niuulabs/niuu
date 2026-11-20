"""A broker restart keeps the assistant span that was open when it stopped (LEX-1982).

The span lives only in memory until its turn ends. A broker whose durable log is too
large to read back keeps its local cache on restart, so a span it never saved was
lost (ios-release, 2026-11-08: 183 parts). On shutdown the broker now logs a
``turn_interrupted`` frame and saves the open span as an interrupted turn; a rebuild
closes the span at the same frame, so both give the same turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx

from niuu.domain.transcript_reducer import (
    TURN_INTERRUPTED_KIND,
    assistant_turn_id,
    reduce_frames,
)
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.conversation_models import turn_record
from skuld.transports import TransportCapabilities

_SID = "restart-open-span-session"
_TS = datetime(2026, 11, 8, 9, 53, tzinfo=UTC)


class _Frame:
    def __init__(self, seq: int, payload: dict) -> None:
        self.session_id = _SID
        self.seq = seq
        self.kind = str(payload.get("type", "unknown"))
        self.payload = payload
        self.request_id = None
        self.ts = _TS


def _broker(tmp_path) -> Broker:
    broker = Broker(
        SkuldSettings(
            session={"id": _SID, "workspace_dir": str(tmp_path), "resume_session_id": "native"},
            transport="subprocess",
            volundr_api_url="http://forge.test",
            event_log_enabled=True,
        )
    )
    broker._transport = MagicMock()
    broker._transport.is_alive = True
    broker._transport.capabilities = TransportCapabilities()
    broker._report_activity_state = AsyncMock()
    broker._report_timeline_event = AsyncMock()
    broker._channels.broadcast = AsyncMock()
    broker._start_trace_span = AsyncMock(return_value=None)
    broker._finish_trace_span = AsyncMock()
    return broker


def _assistant(*blocks: dict) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


async def _run_open_turn(broker: Broker) -> None:
    """A finished turn, then a turn the agent is still working on."""
    await broker._handle_cli_event(_assistant({"type": "text", "text": "Shipped 2520"}))
    await broker._handle_cli_event({"type": "result", "result": "", "stop_reason": "end_turn"})
    await broker._handle_cli_event(_assistant({"type": "text", "text": "Watching the release"}))
    await broker._handle_cli_event(
        _assistant({"type": "tool_use", "id": "toolu_watch", "name": "Bash", "input": {}})
    )


def _logged_frames(broker: Broker) -> list[_Frame]:
    return [_Frame(int(e["seq"]), e["payload"]) for e in broker._event_log_buffer]


def _shape(turns: list[dict]) -> list[tuple]:
    return [
        (
            t["id"],
            t["role"],
            t["content"],
            [p["type"] for p in t["parts"]],
            t["metadata"].get("status"),
        )
        for t in turns
    ]


async def test_shutdown_saves_the_open_span_as_an_interrupted_turn(tmp_path):
    broker = _broker(tmp_path)
    await _run_open_turn(broker)
    assert broker._serialize_in_progress_turn() is not None
    frame_seq = broker._event_log_seq + 1

    await broker._interrupt_open_turn("broker_shutdown")

    span = turn_record(broker._conversation_turns[-1])
    assert broker._event_log_buffer[-2]["seq"] == frame_seq
    assert span["id"] == assistant_turn_id(_SID, frame_seq)
    assert span["content"] == "Watching the release"
    assert [p["type"] for p in span["parts"]] == ["text", "tool_use"]
    assert span["metadata"]["status"] == "interrupted"
    assert broker._serialize_in_progress_turn() is None
    kinds = [e["kind"] for e in broker._event_log_buffer]
    assert kinds[-2:] == [TURN_INTERRUPTED_KIND, "conversation.turn"]
    saved = json.loads(broker._conversation_history_path().read_text())["turns"]
    assert saved[-1]["id"] == span["id"]
    broker._channels.broadcast.assert_awaited_with(
        {"type": TURN_INTERRUPTED_KIND, "reason": "broker_shutdown"}
    )


async def test_a_rebuild_of_the_log_gives_the_turns_the_broker_saved(tmp_path):
    broker = _broker(tmp_path)
    await _run_open_turn(broker)
    await broker._interrupt_open_turn("broker_shutdown")
    # After the restart the agent resumes and finishes new work in a new span.
    await broker._handle_cli_event(_assistant({"type": "text", "text": "Back after restart"}))
    await broker._handle_cli_event({"type": "result", "result": "", "stop_reason": "end_turn"})

    live = [turn_record(t) for t in broker._conversation_turns]
    rebuilt = reduce_frames(_logged_frames(broker)).turns

    assert [t["content"] for t in live] == [
        "Shipped 2520",
        "Watching the release",
        "Back after restart",
    ]
    assert _shape(rebuilt) == _shape(live)


async def test_without_the_frame_a_rebuild_runs_the_span_into_the_next_one(tmp_path):
    """The failure the frame prevents: the span and the next work fold into one turn."""
    broker = _broker(tmp_path)
    await _run_open_turn(broker)
    await broker._interrupt_open_turn("broker_shutdown")
    await broker._handle_cli_event(_assistant({"type": "text", "text": "Back after restart"}))
    await broker._handle_cli_event({"type": "result", "result": "", "stop_reason": "end_turn"})
    frames = [f for f in _logged_frames(broker) if f.kind != TURN_INTERRUPTED_KIND]

    rebuilt = reduce_frames(frames).turns

    assert [t["content"] for t in rebuilt] == [
        "Shipped 2520",
        "Watching the release\nBack after restart",
    ]


async def test_an_idle_broker_logs_nothing_at_shutdown(tmp_path):
    broker = _broker(tmp_path)
    await broker._handle_cli_event(_assistant({"type": "text", "text": "Shipped 2520"}))
    await broker._handle_cli_event({"type": "result", "result": "", "stop_reason": "end_turn"})
    logged = len(broker._event_log_buffer)
    turns = len(broker._conversation_turns)

    await broker._interrupt_open_turn("broker_shutdown")

    assert len(broker._event_log_buffer) == logged
    assert len(broker._conversation_turns) == turns


async def test_shutdown_interrupts_the_open_turn_before_the_log_drains(tmp_path):
    broker = _broker(tmp_path)
    await _run_open_turn(broker)
    drained_with: list[list[str]] = []

    async def stop_event_log():
        drained_with.append([e["kind"] for e in broker._event_log_buffer])

    broker._stop_event_log = stop_event_log
    broker._transport.stop = AsyncMock()

    await broker.shutdown()

    assert drained_with and drained_with[0][-2:] == [TURN_INTERRUPTED_KIND, "conversation.turn"]
    assert broker._conversation_turns[-1].metadata["status"] == "interrupted"


async def test_a_restart_that_cannot_read_the_whole_log_keeps_the_span(tmp_path):
    """ios-release's case: the full read is over budget, so the local cache is kept."""
    before = _broker(tmp_path)
    await _run_open_turn(before)
    await before._interrupt_open_turn("broker_shutdown")

    async def handle(request):
        if request.url.path.endswith("/head"):
            return httpx.Response(200, json={"latest_seq": 10_000})
        return httpx.Response(200, content=b"[" + b" " * 2048 + b"]")

    after = _broker(tmp_path)
    after._settings.history_hydration_max_bytes = 1024
    after._load_conversation_history()
    async with httpx.AsyncClient(
        base_url="http://forge.test", transport=httpx.MockTransport(handle)
    ) as client:
        after._http_client = client
        await after._hydrate_conversation_history()

    assert [t.content for t in after._conversation_turns] == [
        "Shipped 2520",
        "Watching the release",
    ]
    assert after._conversation_turns[-1].metadata["status"] == "interrupted"


async def test_terminal_repaints_in_the_span_do_not_change_its_id(tmp_path):
    """tmux sessions log pane repaints the live span never folds; ids must still agree."""
    broker = _broker(tmp_path)
    await _run_open_turn(broker)
    await broker._handle_cli_event({"type": "terminal_frame", "rows": ["Running..."]})
    await broker._interrupt_open_turn("broker_shutdown")
    await broker._handle_cli_event(_assistant({"type": "text", "text": "Back after restart"}))
    await broker._handle_cli_event({"type": "terminal_frame", "rows": ["Done"]})
    await broker._handle_cli_event({"type": "result", "result": "", "stop_reason": "end_turn"})

    live = [turn_record(t) for t in broker._conversation_turns]
    rebuilt = reduce_frames(_logged_frames(broker)).turns

    assert _shape(rebuilt) == _shape(live)


async def test_a_full_hydration_after_an_interrupt_merges_with_the_cache(tmp_path, caplog):
    """The next restart that can read the whole log agrees with the cache: no skip."""
    before = _broker(tmp_path)
    await _run_open_turn(before)
    await before._handle_cli_event({"type": "terminal_frame", "rows": ["Running..."]})
    await before._interrupt_open_turn("broker_shutdown")
    rows = [
        {
            "session_id": _SID,
            "seq": e["seq"],
            "kind": e["kind"],
            "payload": e["payload"],
            "ts": e["ts"],
        }
        for e in before._event_log_buffer
        if e["kind"] != "conversation.turn"  # the server never serves seeds on /log
    ]
    head = before._event_log_seq

    async def handle(request):
        if request.url.path.endswith("/head"):
            return httpx.Response(200, json={"latest_seq": head})
        after_seq = int(request.url.params["after"])
        return httpx.Response(200, json=[r for r in rows if r["seq"] > after_seq])

    after = _broker(tmp_path)
    after._load_conversation_history()
    async with httpx.AsyncClient(
        base_url="http://forge.test", transport=httpx.MockTransport(handle)
    ) as client:
        after._http_client = client
        with caplog.at_level(logging.INFO, logger="skuld.broker"):
            await after._hydrate_conversation_history()

    assert "hydration skipped" not in caplog.text
    assert f"Hydrated {len(before._conversation_turns)} conversation turns" in caplog.text
    assert _shape([turn_record(t) for t in after._conversation_turns]) == _shape(
        [turn_record(t) for t in before._conversation_turns]
    )
    hydrated = json.loads(after._conversation_history_path().read_text())["turns"]
    assert len(hydrated) == len(before._conversation_turns)


async def test_the_save_waits_for_a_frame_logged_but_not_yet_folded(tmp_path):
    """A frame is logged, then broadcast, then folded: the saved turn must still hold it."""
    broker = _broker(tmp_path)
    await _run_open_turn(broker)
    release = asyncio.Event()

    async def slow_broadcast(frame):
        if frame.get("type") == "assistant":
            await release.wait()

    broker._channels.broadcast = slow_broadcast
    late = asyncio.create_task(
        broker._handle_cli_event(_assistant({"type": "text", "text": "Still watching"}))
    )
    await asyncio.sleep(0)
    assert broker._event_log_buffer[-1]["payload"]["type"] == "assistant"  # logged, not folded
    save = asyncio.create_task(broker._interrupt_open_turn("broker_shutdown"))
    await asyncio.sleep(0)
    assert not save.done()  # waits for the frame in flight
    # A frame that arrives while the save waits is held until the boundary is logged.
    after = asyncio.create_task(
        broker._handle_cli_event(_assistant({"type": "text", "text": "After the boundary"}))
    )
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(late, save, after)

    span = turn_record(broker._conversation_turns[-1])
    assert "Still watching" in span["content"]
    assert "After the boundary" not in span["content"]
    kinds = [e["kind"] for e in broker._event_log_buffer]
    assert kinds.index(TURN_INTERRUPTED_KIND) < len(kinds) - 1
    assert broker._event_log_buffer[-1]["payload"]["message"]["content"][0]["text"] == (
        "After the boundary"
    )
    await broker._handle_cli_event({"type": "result", "result": "", "stop_reason": "end_turn"})
    live = [turn_record(t) for t in broker._conversation_turns]
    assert _shape(reduce_frames(_logged_frames(broker)).turns) == _shape(live)


async def test_a_nested_event_inside_a_handler_is_not_held(tmp_path):
    broker = _broker(tmp_path)
    seen = []
    real = broker._handle_cli_event_now

    async def outer(data):
        if data.get("type") == "outer":
            await broker._handle_cli_event({"type": "inner"})
        seen.append(data["type"])
        await real(data) if data["type"] not in ("outer", "inner") else None

    broker._handle_cli_event_now = outer
    await asyncio.wait_for(broker._handle_cli_event({"type": "outer"}), timeout=5)

    assert seen == ["inner", "outer"]
    assert broker._cli_events_in_flight == 0


async def test_a_task_that_outlives_its_handler_is_gated(tmp_path):
    broker = _broker(tmp_path)
    spawned: list[asyncio.Task] = []
    go = asyncio.Event()
    real = broker._handle_cli_event_now

    async def later():
        await go.wait()
        await broker._handle_cli_event(_assistant({"type": "text", "text": "from a child"}))

    async def handler(data):
        if data.get("type") == "spawn":
            spawned.append(asyncio.create_task(later()))
            return
        await real(data)

    broker._handle_cli_event_now = handler
    await broker._handle_cli_event({"type": "spawn"})  # the handler returns; its task lives on
    await _run_open_turn(broker)
    broker._cli_events_open.clear()  # shutdown is saving
    go.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not spawned[0].done()  # held at the gate, not run past it
    broker._cli_events_open.set()
    await spawned[0]
    assert "from a child" in broker._pending_assistant_content


async def test_no_event_reaches_a_client_before_the_boundary(tmp_path):
    broker = _broker(tmp_path)
    await _run_open_turn(broker)
    sent: list[str] = []
    release = asyncio.Event()

    async def broadcast(frame):
        if frame.get("type") == TURN_INTERRUPTED_KIND:
            await release.wait()
        sent.append(frame.get("type"))

    broker._channels.broadcast = broadcast
    save = asyncio.create_task(broker._interrupt_open_turn("broker_shutdown"))
    await asyncio.sleep(0)
    after = asyncio.create_task(
        broker._handle_cli_event(_assistant({"type": "text", "text": "After the boundary"}))
    )
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(save, after)

    assert sent.index(TURN_INTERRUPTED_KIND) < sent.index("assistant")
