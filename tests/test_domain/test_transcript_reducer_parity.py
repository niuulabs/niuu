"""INV-4 fold-parity: the LIVE incremental fold == the BATCH rebuild over the durable log.

This is the test the SRD (§7 INV-4, §6 FR-3) makes load-bearing: there must be ONE folding
contract. We drive the SAME logical frame sequence two ways —

  (a) INCREMENTALLY through the live broker's ``_handle_cli_event`` (frame-by-frame, the way
      deltas stream), capturing both the resulting ``_conversation_turns`` AND the durable
      ``_event_log_buffer`` the broker persisted; and
  (b) in BATCH through ``volundr...transcript_rebuild.rebuild_turns`` over those EXACT persisted
      entries —

and assert the two turn lists are EQUAL (ids, content, parts ordering, metadata). Because both
paths now drive the shared ``niuu.domain.transcript_reducer`` transitions, equality holds by
construction. The cases below include the Epic-A ``user`` + ``user_confirmed`` dedup and an
interrupted/partial (crash-mid-turn) turn.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports import TransportCapabilities
from volundr.domain.models import SessionLogEntry
from volundr.domain.services.transcript_rebuild import rebuild_turns

SID = "test-session"
# The reducer derives turn ids from ``str(session_id)``; the live broker uses ``self.session_id``
# (the raw "test-session" string). Pin the rebuild rows to the SAME value so ids line up exactly
# as they do in production (where the broker id and the durable-log session_id are one value).
_SID_FOR_ENTRY = SID


def _broker(tmp_path) -> Broker:
    settings = SkuldSettings(
        session={"id": SID, "workspace_dir": str(tmp_path)},
        transport="subprocess",
        volundr_api_url="http://volundr.test",
    )
    b = Broker(settings=settings)
    b._transport = MagicMock()
    b._transport.is_alive = True
    b._transport.capabilities = TransportCapabilities()
    # Neutralise fire-and-forget reporting (httpx POSTs to a fake URL) so the fold runs
    # cleanly — we are pinning the FOLD, not the side-channels.
    b._report_activity_state = AsyncMock()
    b._report_usage = AsyncMock()
    b._report_timeline_event = AsyncMock()
    b._on_result_publish_mesh = AsyncMock()
    return b


def _entries_from_buffer(b: Broker) -> list[SessionLogEntry]:
    """Turn the broker's persisted durable-log buffer into the rows the rebuild path reads.

    This is the actual durable record — exactly the RAW frames a crash-rebuild / cold-read
    folds. We drop the ``conversation.turn`` rows the live fold also persists as a side-effect:
    a crash-rebuild folds the raw transport frames (the work that survived); the
    ``conversation.turn`` rows are the live fold's OWN output, so feeding them back would mean
    comparing the live fold to itself. The honest INV-4 question is "does folding the raw
    durable frames reproduce the live turns" — that is what this drives.
    """
    rows: list[SessionLogEntry] = []
    for e in b._event_log_buffer:
        if e["kind"] == "conversation.turn":
            continue
        ts = e.get("ts")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        rows.append(
            SessionLogEntry(
                # The durable log's session_id must equal the broker's self.session_id — that is
                # the SAME value the live fold uses in its deterministic turn id, so the rebuild
                # derives the SAME id. (In production they are one value; pin them here too.)
                session_id=_SID_FOR_ENTRY,
                seq=e["seq"],
                kind=e["kind"],
                payload=e["payload"],
                ts=ts or datetime.now(UTC),
                role=e.get("role"),
                request_id=e.get("request_id"),
            )
        )
    return rows


def _live_turns(b: Broker) -> list[dict]:
    """The live fold, normalised to the rebuild's turn-dict shape (drop participant chrome)."""
    out: list[dict] = []
    for t in b._conversation_turns:
        out.append(
            {
                "id": t.id,
                "role": t.role,
                "content": t.content,
                "parts": t.parts,
                "metadata": t.metadata,
                "visibility": t.visibility,
            }
        )
    return out


def _rebuilt_turns(b: Broker) -> list[dict]:
    res = rebuild_turns(_entries_from_buffer(b))
    out: list[dict] = []
    for t in res.turns:
        # The rebuild emits created_at; the live ConversationTurn's created_at is wall-clock at
        # construction time, so timestamps are not comparable. Parity is about id/content/parts/
        # metadata — drop created_at on both sides.
        out.append(
            {
                "id": t["id"],
                "role": t["role"],
                "content": t["content"],
                "parts": t["parts"],
                "metadata": t["metadata"],
                "visibility": t.get("visibility", "public"),
            }
        )
    return out


async def _drive(b: Broker, frames: list[dict]) -> None:
    for frame in frames:
        await b._handle_cli_event(frame)


# --------------------------------------------------------------------------- delivery parity


def _delivery_broker(tmp_path) -> Broker:
    """A broker wired for the REAL delivery path: a user message goes through
    ``_dispatch_browser_message`` -> bounded-retry delivery -> ACK, and EVERY steering frame
    (user / user_confirmed / user_active / user_delivery_failed) lands in the durable buffer."""
    settings = SkuldSettings(
        session={"id": SID, "workspace_dir": str(tmp_path)},
        transport="sdk",
        volundr_api_url="http://volundr.test",
        delivery={"max_attempts": 2, "initial_backoff_seconds": 0.0, "max_backoff_seconds": 0.0},
    )
    b = Broker(settings=settings)
    b._transport = AsyncMock()
    b._transport.is_alive = True
    b._transport.capabilities = TransportCapabilities()
    b._transport.is_turn_active = False
    b._apply_retrieval_reflex = AsyncMock(side_effect=lambda m: m)
    b._report_activity_state = AsyncMock()
    b._report_usage = AsyncMock()
    b._report_timeline_event = AsyncMock()
    b._complete_trace_span = AsyncMock()
    # The shared event-log buffer IS the durable record we rebuild from — leave it live.
    b._save_conversation_history = MagicMock()
    return b


async def _settle_delivery() -> None:
    """Await the fire-and-forget ``transport-deliver-*`` task dispatch schedules."""
    import asyncio

    tasks = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and t.get_name().startswith("transport-deliver-")
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _user_turn(turns: list[dict]) -> dict:
    return next(t for t in turns if t["role"] == "user")


@pytest.mark.asyncio
async def test_parity_delivery_state_active(tmp_path):
    """INV-4 + INV-7: a user message driven to delivered+active stamps steering_state on the
    LIVE turn; rebuilding from the durable log must reconstruct the SAME steering_state — the
    delivery state is not a live-only, on-disk fact (it is folded from the logged ACK frames)."""
    b = _delivery_broker(tmp_path)

    await b._dispatch_browser_message({"content": "steer the agent", "request_id": "rq-1"})
    await _settle_delivery()
    # The agent consumed the steer (the correlated UserPromptSubmit / turn-started signal),
    # which the broker logs as user_active and stamps the live turn active.
    live_user = next(t for t in b._conversation_turns if t.role == "user")
    await b._activate_user_turn({"msg_id": live_user.id, "request_id": "rq-1"})

    live = _live_turns(b)
    rebuilt = _rebuilt_turns(b)

    assert _user_turn(live)["metadata"].get("steering_state") == "active"
    # The rebuilt user turn carries the SAME steering_state, reconstructed purely from the log.
    assert _user_turn(rebuilt)["metadata"].get("steering_state") == "active"
    assert _user_turn(live)["metadata"].get("steering_state") == _user_turn(rebuilt)[
        "metadata"
    ].get("steering_state")
    assert _user_turn(live)["id"] == _user_turn(rebuilt)["id"]


@pytest.mark.asyncio
async def test_parity_delivery_state_failed(tmp_path):
    """INV-4 + INV-7: a user message whose delivery terminally fails flips the LIVE turn to a
    visible ``failed`` state; the rebuild reconstructs ``failed`` from the logged
    user_delivery_failed frame — never silently losing it to a bare ``pending``."""
    b = _delivery_broker(tmp_path)
    b._transport.send_message = AsyncMock(side_effect=RuntimeError("wedged forever"))

    await b._dispatch_browser_message({"content": "this will fail", "request_id": "rq-2"})
    await _settle_delivery()

    live = _live_turns(b)
    rebuilt = _rebuilt_turns(b)

    assert _user_turn(live)["metadata"].get("steering_state") == "failed"
    assert _user_turn(rebuilt)["metadata"].get("steering_state") == "failed"
    assert _user_turn(live)["id"] == _user_turn(rebuilt)["id"]


@pytest.mark.asyncio
async def test_parity_full_turn_text_reasoning_tools_user_and_result(tmp_path):
    """Assistant text + reasoning + tool_use/tool_result + a user turn + a result with usage."""
    b = _broker(tmp_path)
    frames = [
        # one logical user turn arriving via the transport, carrying a uuid
        {"type": "user", "uuid": "U-1", "message": {"role": "user", "content": "do the thing"}},
        # assistant frame: reasoning + a tool_use call
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "let me think"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"cmd": "ls"}},
                ]
            },
        },
        # tool_result-only user event enriches the OPEN assistant turn (not a new turn)
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        },
        # streaming text deltas for the assistant's prose
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello "}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "world"}},
        # result closes the turn with usage/cost/model
        {
            "type": "result",
            "result": "",
            "stop_reason": "end_turn",
            "modelUsage": {"claude-x": {"inputTokens": 10, "outputTokens": 5, "costUSD": 0.002}},
        },
    ]
    await _drive(b, frames)

    live = _live_turns(b)
    rebuilt = _rebuilt_turns(b)

    assert [t["role"] for t in live] == ["user", "assistant"]
    assert live == rebuilt
    # the metadata schema is the UNIFIED one (usage/cost/model), readable by the UI
    asst_meta = live[1]["metadata"]
    assert asst_meta["cost"] == 0.002
    assert asst_meta["model"] == "claude-x"
    assert "usage" in asst_meta
    # parts order is identical on both paths: the assistant frame's blocks (reasoning, then
    # tool_use) first, then the tool_result enriching the open turn.
    assert [p["type"] for p in live[1]["parts"]] == ["reasoning", "tool_use", "tool_result"]


@pytest.mark.asyncio
async def test_parity_preserves_codex_agent_message_boundaries_and_phases(tmp_path):
    b = _broker(tmp_path)
    frames = [
        {"type": "user", "uuid": "U-2", "message": {"role": "user", "content": "work"}},
        {
            "type": "content_block_start",
            "content_block": {"type": "text", "id": "msg-1", "phase": "commentary"},
        },
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "First."}},
        {"type": "content_block_stop"},
        {
            "type": "content_block_start",
            "content_block": {"type": "text", "id": "msg-2", "phase": "final_answer"},
        },
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Second."}},
        {"type": "content_block_stop"},
        {"type": "result", "result": "", "stop_reason": "end_turn", "modelUsage": {}},
    ]
    await _drive(b, frames)

    live = _live_turns(b)
    rebuilt = _rebuilt_turns(b)

    assert live == rebuilt
    assert live[1]["content"] == "First.\n\nSecond."
    assert live[1]["parts"] == [
        {"type": "text", "text": "First.", "id": "msg-1", "phase": "commentary"},
        {"type": "text", "text": "Second.", "id": "msg-2", "phase": "final_answer"},
    ]


@pytest.mark.asyncio
async def test_parity_tool_use_only_turn_closed_by_nonempty_result(tmp_path):
    """B-2 / INV-4 / FR-3: a turn that streamed NO assistant text — only a tool_use block —
    closed by a result carrying text. The live viewer saw the result text as the turn content;
    the rebuild MUST reproduce that exact content. (Pre-fix the live path injected "DONE" but the
    shared reducer dropped it, because the turn's non-empty parts made it not ``is_empty``.)
    """
    b = _broker(tmp_path)
    frames = [
        {"type": "user", "uuid": "U-3", "message": {"role": "user", "content": "run it"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t9", "name": "Bash", "input": {"cmd": "make"}},
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t9", "content": "ok"}]},
        },
        # result closes the turn with text but the turn streamed no assistant prose
        {
            "type": "result",
            "result": "DONE",
            "stop_reason": "end_turn",
            "modelUsage": {"claude-x": {"inputTokens": 3, "outputTokens": 1, "costUSD": 0.001}},
        },
    ]
    await _drive(b, frames)

    live = _live_turns(b)
    rebuilt = _rebuilt_turns(b)

    assert [t["role"] for t in live] == ["user", "assistant"]
    # the live content is the result text...
    assert live[1]["content"] == "DONE"
    # ...and the rebuild reproduces it EXACTLY (the single source of truth, no fork)
    assert rebuilt[1]["content"] == "DONE"
    assert live == rebuilt


@pytest.mark.asyncio
async def test_parity_tool_use_only_turn_result_content_block_text(tmp_path):
    """B-2: same shape, but the result text lives in a ``content`` text block (no top-level
    ``result`` string). Both paths must fold that block's text into the turn content."""
    b = _broker(tmp_path)
    frames = [
        {"type": "user", "uuid": "U-4", "message": {"role": "user", "content": "go"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t5", "name": "Bash", "input": {}},
                ]
            },
        },
        {
            "type": "result",
            "result": "",
            "content": [{"type": "text", "text": "all green"}],
            "modelUsage": {},
        },
    ]
    await _drive(b, frames)

    live = _live_turns(b)
    rebuilt = _rebuilt_turns(b)

    assert live[-1]["content"] == "all green"
    assert rebuilt[-1]["content"] == "all green"
    assert live == rebuilt


@pytest.mark.asyncio
async def test_parity_user_confirmed_and_user_dedup_single_turn(tmp_path):
    """Epic-A carry-over: ONE human message logged as BOTH `user` and `user_confirmed`
    (same id/content) folds to a SINGLE user turn on both paths — never a doubled turn.
    """
    b = _broker(tmp_path)
    msg_id = "MSG-7"
    # The broker browser path persists the user frame (uuid=msg_id) AND a user_confirmed
    # broker frame (id=msg_id). Feed both as durable frames via _handle_cli_event so the live
    # fold sees them exactly as the durable log records them.
    frames = [
        {"type": "user", "uuid": msg_id, "message": {"role": "user", "content": "ship it"}},
        {
            "type": "user_confirmed",
            "id": msg_id,
            "content": "ship it",
            "request_id": None,
            "steering_state": "pending",
        },
    ]
    await _drive(b, frames)

    live = _live_turns(b)
    rebuilt = _rebuilt_turns(b)

    # exactly one user turn (dedup), and the two paths agree
    assert [t["role"] for t in rebuilt] == ["user"]
    assert rebuilt[0]["content"] == "ship it"
    # the live path only mints a turn for the raw `user` frame; user_confirmed is a broker echo.
    # both paths converge on the same single user turn id (the carried msg_id).
    assert rebuilt[0]["id"] == msg_id
    assert live == rebuilt


@pytest.mark.asyncio
async def test_parity_interrupted_partial_turn(tmp_path):
    """A crash-mid-turn: assistant deltas stream, NO terminating result. Both paths flush one
    interrupted assistant turn with the same id/content/metadata.
    """
    b = _broker(tmp_path)
    frames = [
        {"type": "user", "uuid": "U-2", "message": {"role": "user", "content": "build it"}},
        {"type": "assistant", "message": {"content": []}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial "}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "answer"}},
    ]
    await _drive(b, frames)
    # End-of-stream flush of the in-progress turn is what the rebuild does automatically; the
    # live broker flushes the open turn on a terminating event. Simulate the boundary the same
    # way the live path closes a turn at stream end: flush the pending assistant accumulator.
    b._flush_pending_assistant_turn(metadata={"status": "interrupted"})

    live = _live_turns(b)
    rebuilt = _rebuilt_turns(b)

    assert [t["role"] for t in live] == ["user", "assistant"]
    assert live[1]["content"] == "partial answer"
    assert live[1]["metadata"]["status"] == "interrupted"
    assert live == rebuilt
