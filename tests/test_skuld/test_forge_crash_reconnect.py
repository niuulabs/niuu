"""Scenario Group D — CRASH & RECONNECT (forge tmux harness).

These exercise the broker's durability + reconnect-replay contracts against a
REAL tmux session driving ``fakeagent``:

  * D1 — client crash mid-turn -> reconnect: the in-flight turn survives the WS
    death and the NEW client sees the assistant state + the turn completing.
  * D2 — crash WITH a question open -> reconnect re-surfaces the answerable
    ask_user_question (tmux lock-state guard), the new client answers it, and the
    agent unblocks (ask_user_resolved). HIGHEST value.
  * D3 — agent crash mid-turn -> the durable event-log rebuilds a partial,
    ``metadata.status=="interrupted"`` assistant turn with deterministic uuid5
    ids stable across two rebuilds (Bug 2).
  * D4 — (default tier, pure reducer) authoritative conversation.turn rows win
    over overlapping raw frames sharing a request_id (no double-count).
  * D5 — reconnect does NOT re-surface an ALREADY-answered question.
  * D6 — (best-effort) conversation endpoint fallback to the rebuilt log; left as
    a documented xfail rather than a fragile Postgres-wired test.

Integration tests drive real tmux and are deselected by the default addopts;
run with ``-m integration`` and ``SKULD__TMUX_REMOTE_CONTROL=0``.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest

from tests.support.forge import BrokerHarness


def _require_tmux() -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")


def _assistant_says(frame: dict, needle: str) -> bool:
    if frame.get("type") != "assistant":
        return False
    message = frame.get("message", {})
    content = message.get("content", []) if isinstance(message, dict) else []
    if isinstance(content, str):
        return needle in content
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict) and needle in str(block.get("text", "")):
            return True
    return False


def _terminal_text(frame: dict) -> str:
    rows = frame.get("rows")
    if isinstance(rows, list):
        return "\n".join(str(r) for r in rows)
    return str(frame.get("text", ""))


def _frame_shows(frame: dict, needle: str) -> bool:
    """True if a frame surfaces ``needle`` — as an assistant text block OR as
    visible terminal-pane text (the tmux transport streams ``work:`` ticks as
    terminal_frame rows, not assistant blocks)."""
    if _assistant_says(frame, needle):
        return True
    if frame.get("type") in {"terminal_frame", "terminal_snapshot"}:
        return needle in _terminal_text(frame)
    return False


def _turn_text(turn: dict) -> str:
    content = turn.get("content", "")
    if isinstance(content, str) and content:
        return content
    parts = turn.get("parts", [])
    if not isinstance(parts, list):
        return str(content)
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            texts.append(str(part.get("text", "")))
    return "\n".join(texts) or str(content)


# --------------------------------------------------------------------------- D1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_d1_client_crash_mid_turn_reconnect_sees_inflight_and_completes() -> None:
    """D1: kill the client mid ``work:`` turn; a fresh client sees the in-flight
    assistant output and the turn still closes with a result."""
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        first = await h.connect()

        # A long, interruptible turn — the agent ticks "...working N" for ~3s.
        first.send({"type": "message", "content": "work:3"})

        # Wait until the turn is actually in flight (at least one tick streamed
        # to the terminal pane), then abruptly crash the browser socket mid-turn.
        await first.wait_for(
            lambda frames: any(_frame_shows(f, "working") for f in frames),
            timeout=8.0,
        )
        await h.kill(first)

        # A fresh browser connects while the SAME turn is still running.
        second = await h.connect()

        # The new client receives in-flight assistant state for the live turn
        # (more ticks stream to it) AND the turn completes with 'done' + result.
        await second.wait_for(
            lambda frames: any(_frame_shows(f, "working") for f in frames),
            timeout=8.0,
        )
        await second.wait_for(
            lambda frames: any(_frame_shows(f, "done") for f in frames),
            timeout=8.0,
        )
        await second.wait_for(
            lambda frames: any(f.get("type") == "result" for f in frames),
            timeout=8.0,
        )

        results = second.frames_of_type("result")
        assert results, "fresh client never observed the turn closing"

        # The durable log captured the whole turn regardless of the WS churn.
        kinds = [e.get("kind") for e in h.event_log]
        assert "result" in kinds, f"durable log missing result; kinds={kinds}"


# --------------------------------------------------------------------------- D2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_d2_crash_with_question_open_reconnect_resurfaces_and_unblocks() -> None:
    """D2 (highest value): a question is open, the client crashes, a fresh client
    reconnects and the pending ask_user_question is REPLAYED to it (answerable),
    it answers, ask_user_resolved fires and the agent unblocks."""
    _require_tmux()

    async with BrokerHarness(
        hooks=True,
        idle_timeout_s=0.3,
        ask_user_question_enabled=True,
        boot="ask:Db|Which database?|Postgres;SQLite",
    ) as h:
        first = await h.connect()

        # The boot directive fires an AskUserQuestion PreToolUse hook; the tmux
        # bridge surfaces it as a structured ask_user_question. Wait for it.
        ask = await first.wait_for_type("ask_user_question", timeout=8.0)
        request_id = str(ask.get("request_id") or "")
        assert request_id, f"ask_user_question carried no request_id: {ask}"

        # Broker is tracking it for reconnect replay.
        assert request_id in h.broker._pending_ask_user_questions

        # Crash the browser WHILE the agent is blocked on the answer.
        await h.kill(first)

        # A fresh browser reconnects mid-block — it MUST get the answerable card,
        # not a frozen/dead session (the tmux lock-state reconnect guard).
        second = await h.connect()
        replayed = await second.wait_for_type("ask_user_question", timeout=8.0)
        assert str(replayed.get("request_id") or "") == request_id
        # The replayed frame is fully answerable (carries its questions).
        assert replayed.get("questions"), "replayed ask_user_question lost its questions"

        # The new client answers it.
        second.send(
            {
                "type": "ask_user_answer",
                "request_id": request_id,
                "answers": [{"answer": "Postgres"}],
            }
        )

        # The bridge resolves the question and the agent unblocks: it echoes the
        # chosen option ('chose: Postgres') and finishes the turn.
        await second.wait_for(
            lambda frames: any(
                f.get("type") == "ask_user_resolved"
                and str(f.get("request_id") or "") == request_id
                for f in frames
            ),
            timeout=8.0,
        )
        await second.wait_for(
            lambda frames: any(_assistant_says(f, "chose: Postgres") for f in frames),
            timeout=8.0,
        )

        # Server-side state cleared: no longer pending, no longer awaiting input.
        assert request_id not in h.broker._pending_ask_user_questions


# --------------------------------------------------------------------------- D3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_d3_agent_crash_mid_turn_rebuilds_interrupted_transcript() -> None:
    """D3 (Bug 2): SIGKILL the agent mid ``work:`` turn; the durable log rebuilds
    a partial assistant turn flagged interrupted, with deterministic uuid5 ids
    stable across two rebuilds."""
    _require_tmux()

    from volundr.domain.services.transcript_rebuild import rebuild_turns

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send({"type": "message", "content": "work:5"})

        # Wait until the turn is genuinely in flight (ticks streamed to the
        # pane), then kill the agent process inside tmux so the turn never
        # reaches a result.
        await client.wait_for(
            lambda frames: any(_frame_shows(f, "working") for f in frames),
            timeout=8.0,
        )
        h.crash_agent()

        # Give the broker a beat to settle (no result should arrive).
        await asyncio.sleep(0.5)

        entries = h.event_log_entries()

    # No terminating result for the work turn → the reducer flushes the pending
    # assistant span as an interrupted turn.
    rebuilt = rebuild_turns(entries)
    assert rebuilt.turns, "rebuild produced no turns from the durable log"

    interrupted = [t for t in rebuilt.turns if t.get("metadata", {}).get("status") == "interrupted"]
    assert interrupted, (
        "expected a partial assistant turn flagged interrupted; "
        f"turns={[t.get('metadata') for t in rebuilt.turns]}"
    )
    assert rebuilt.partial is True
    # The partial turn carries the in-flight assistant work.
    assert any("working" in _turn_text(t) for t in interrupted), (
        f"interrupted turn lost the in-flight text; turns={interrupted}"
    )

    # Deterministic + idempotent: a second rebuild of the SAME log is byte-identical.
    again = rebuild_turns(entries)
    assert [t["id"] for t in again.turns] == [t["id"] for t in rebuilt.turns]
    assert again.turns == rebuilt.turns


# --------------------------------------------------------------------------- D5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_d5_reconnect_does_not_resurface_settled_question() -> None:
    """D5: answer a question, THEN reconnect — the settled question is NOT
    replayed to the fresh client (the answer dropped the replay entry)."""
    _require_tmux()

    async with BrokerHarness(
        hooks=True,
        idle_timeout_s=0.3,
        ask_user_question_enabled=True,
        boot="ask:Db|Which database?|Postgres;SQLite",
    ) as h:
        first = await h.connect()

        ask = await first.wait_for_type("ask_user_question", timeout=8.0)
        request_id = str(ask.get("request_id") or "")
        assert request_id

        # Answer it on the live connection.
        first.send(
            {
                "type": "ask_user_answer",
                "request_id": request_id,
                "answers": [{"answer": "Postgres"}],
            }
        )
        await first.wait_for(
            lambda frames: any(
                f.get("type") == "ask_user_resolved"
                and str(f.get("request_id") or "") == request_id
                for f in frames
            ),
            timeout=8.0,
        )
        # Replay entry is gone after the answer.
        assert request_id not in h.broker._pending_ask_user_questions

        # Now drop the client cleanly and reconnect a fresh one.
        await h.drop(first)
        second = await h.connect()

        # Give any (erroneous) replay a chance to land, then assert NONE arrived.
        await asyncio.sleep(0.4)
        replayed = [
            f
            for f in second.frames_of_type("ask_user_question")
            if str(f.get("request_id") or "") == request_id
        ]
        assert not replayed, f"settled question was wrongly re-surfaced on reconnect: {replayed}"


# --------------------------------------------------------------------------- D7


@pytest.mark.integration
@pytest.mark.asyncio
async def test_d7_reconnect_conversation_history_carries_head_seq_cursor() -> None:
    """D7 (SRD FR-6 / INV-6 live side): a reconnecting client gets a
    conversation_history frame carrying the durable-log HEAD seq, and can stream
    the live tail from head+1 with no gap and no duplicate.

    We run a turn, reconnect a fresh client, capture the head_seq on its
    conversation_history frame, then assert the durable log's frames AFTER that
    head are exactly the tail (seq strictly > head, contiguous from head+1) —
    i.e. the client resuming from head+1 sees every later frame once, none twice.
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        first = await h.connect()
        first.send({"type": "message", "content": "hi"})
        await first.wait_for(
            lambda frames: any(f.get("type") == "result" for f in frames),
            timeout=8.0,
        )
        await h.drop(first)

        # A fresh client reconnects: its conversation_history frame carries the
        # head seq the broker has logged so far.
        second = await h.connect()
        history = await second.wait_for_type("conversation_history", timeout=8.0)
        assert "head_seq" in history, f"reconnect frame missing head_seq cursor: {history}"
        head_seq = history["head_seq"]
        assert isinstance(head_seq, int) and head_seq >= 1
        # The cursor equals the broker's durable-log head at reconnect time.
        assert head_seq == h.broker._event_log_seq

        # Drive a SECOND turn so the log grows past the captured head. Gate on the
        # durable-log head itself advancing past the captured head_seq — not merely
        # on a ``result`` frame appearing — so the wait can never short-circuit on a
        # replayed/stale frame and proceed without a genuinely-new turn ever running.
        second.send({"type": "message", "content": "again"})
        await second.wait_for(
            lambda frames: (
                h.broker._event_log_seq > head_seq
                and sum(f.get("type") == "result" for f in frames) >= 1
            ),
            timeout=8.0,
        )

        # Snapshot the durable log WHILE the broker (and its buffer) is still alive —
        # ``__aexit__`` tears the broker down and ``h.event_log`` then reads empty.
        # The tail (frames strictly after the captured head) is contiguous from
        # head+1 — a client resuming at head+1 sees each later frame exactly once,
        # no gap, no duplicate (INV-6 live side).
        log = h.event_log
        tail_seqs = sorted(e["seq"] for e in log if e["seq"] > head_seq)
        assert tail_seqs, "expected the durable log to grow past the reconnect head"
        assert tail_seqs == list(range(head_seq + 1, head_seq + 1 + len(tail_seqs)))
        # And nothing at/below the head is re-emitted into the tail (no dup).
        assert min(tail_seqs) == head_seq + 1


# --------------------------------------------------------------------------- D4


def _log_entry(seq, kind, payload, *, request_id=None, role=None):
    from datetime import UTC, datetime

    from volundr.domain.models import SessionLogEntry

    return SessionLogEntry(
        session_id=uuid.UUID("0bc95c96-3bb0-4096-beaa-536267b67e6f"),
        seq=seq,
        kind=kind,
        payload=payload,
        ts=datetime(2026, 6, 24, 12, 0, seq % 60, tzinfo=UTC),
        role=role,
        request_id=request_id,
    )


def test_d4_authoritative_turns_win_no_double_count_of_raw_frames() -> None:
    """D4 (default tier, pure reducer): when a conversation.turn row AND overlapping
    raw frames share the same request_id, the authoritative row wins and the raw
    frames are NOT double-counted into a second assistant turn."""
    from volundr.domain.services.transcript_rebuild import rebuild_turns

    rows = [
        # Authoritative SDK rows.
        _log_entry(
            1,
            "conversation.turn",
            {
                "type": "conversation.turn",
                "turn": {"id": "u1", "role": "user", "content": "do it", "uuid": "U1"},
            },
            request_id="req-user",
        ),
        _log_entry(
            2,
            "conversation.turn",
            {
                "type": "conversation.turn",
                "turn": {"id": "a1", "role": "assistant", "content": "did it", "parts": []},
            },
            request_id="req-asst",
        ),
        # Overlapping RAW frames for the SAME assistant request_id — must be skipped,
        # not folded into a second assistant turn.
        _log_entry(
            3,
            "assistant",
            {"message": {"content": [{"type": "text", "text": "did it"}]}},
            request_id="req-asst",
        ),
        _log_entry(
            4,
            "content_block_delta",
            {"delta": {"type": "text_delta", "text": " (extra)"}},
            request_id="req-asst",
        ),
        _log_entry(
            5,
            "result",
            {"result": "did it", "modelUsage": {}, "stop_reason": "end_turn"},
            request_id="req-asst",
        ),
    ]

    res = rebuild_turns(rows)

    # Exactly the two authoritative turns — the overlapping raw frames did NOT
    # produce a duplicate assistant turn.
    assert [t["role"] for t in res.turns] == ["user", "assistant"]
    assert res.turns[0]["content"] == "do it"
    assert res.turns[1]["content"] == "did it"
    # The authoritative assistant turn passes through verbatim (no " (extra)" leak
    # from the skipped raw delta).
    assert "extra" not in res.turns[1]["content"]
    assert res.partial is False

    # And a raw frame whose request_id is NOT folded still reduces normally
    # (proving the skip is request_id-scoped, not a blanket suppression).
    rows_unfolded = rows + [
        _log_entry(6, "user", {"uuid": "U2", "message": {"content": "again"}}),
        _log_entry(
            7,
            "content_block_delta",
            {"delta": {"type": "text_delta", "text": "sure"}},
            request_id="req-other",
        ),
        _log_entry(
            8,
            "result",
            {"result": "sure", "modelUsage": {}},
            request_id="req-other",
        ),
    ]
    res2 = rebuild_turns(rows_unfolded)
    assert [t["role"] for t in res2.turns] == ["user", "assistant", "user", "assistant"]
    assert res2.turns[3]["content"] == "sure"


# --------------------------------------------------------------------------- D6


def test_d6_conversation_endpoint_fallback_to_rebuilt_log() -> None:
    """D6: GET /conversation fallback-to-rebuilt-log — now a REAL, passing test.

    The dead-session conversation fallback lives at the volundr REST tier (real
    ForgeService + SessionArchiveService over in-memory repos, NO Postgres), where
    the endpoint can actually be exercised. It asserts the HTTP turns equal
    ``rebuild_turns(read_after(0))`` — the same reducer the live fold uses (INV-4 /
    INV-9). The broker-tier harness can't host the volundr endpoint, so this marker
    test simply documents where the coverage moved and stays green.
    """
    import importlib

    mod = importlib.import_module("tests.test_adapters.test_rest_conversation_fallback")
    assert hasattr(mod, "test_dead_session_conversation_falls_back_to_rebuilt_log")
    assert hasattr(mod, "test_dead_session_conversation_uses_shared_reducer_output")
