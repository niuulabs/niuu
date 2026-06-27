"""Unit tests for the pure transcript reducer (BUG-2: tmux/crash transcript rebuild)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from volundr.domain.models import SessionLogEntry
from volundr.domain.services.transcript_rebuild import rebuild_turns

SID = uuid.UUID("0bc95c96-3bb0-4096-beaa-536267b67e6f")


def _entry(
    seq: int, kind: str, payload: dict, request_id: str | None = None, role: str | None = None
):
    return SessionLogEntry(
        session_id=SID,
        seq=seq,
        kind=kind,
        payload=payload,
        ts=datetime(2026, 6, 23, 11, 30, seq % 60, tzinfo=UTC),
        role=role,
        request_id=request_id,
    )


def _conv_turn(seq, turn, request_id=None):
    return _entry(
        seq, "conversation.turn", {"type": "conversation.turn", "turn": turn}, request_id=request_id
    )


def test_sdk_conversation_turn_passthrough_and_no_refold():
    rows = [
        _conv_turn(
            1,
            {"id": "u1", "role": "user", "content": "do the thing", "uuid": "U1"},
            request_id="r1",
        ),
        _conv_turn(
            2, {"id": "a1", "role": "assistant", "content": "did it", "parts": []}, request_id="r2"
        ),
        # a raw assistant frame for the SAME request_id as a1 must NOT re-fold:
        _entry(
            3,
            "assistant",
            {"message": {"content": [{"type": "text", "text": "did it"}]}},
            request_id="r2",
        ),
    ]
    res = rebuild_turns(rows)
    assert [t["role"] for t in res.turns] == ["user", "assistant"]
    assert res.turns[0]["content"] == "do the thing"
    assert res.turns[1]["content"] == "did it"
    # conversation.turn rows pass through VERBATIM (byte-identical SDK reload) — the
    # reducer does not inject fields the stored turn didn't have.
    assert res.turns[0] == {"id": "u1", "role": "user", "content": "do the thing", "uuid": "U1"}
    assert res.partial is False


def test_seed_double_log_dedup_yields_one_user_turn():
    # The incident's most common double-count: the seed human turn is written BOTH as a
    # conversation.turn AND as a raw `user` frame carrying the same uuid.
    rows = [
        _conv_turn(1, {"id": "u1", "role": "user", "content": "fix the bug", "uuid": "SEED"}),
        _entry(2, "user", {"uuid": "SEED", "message": {"content": "fix the bug"}}),
    ]
    res = rebuild_turns(rows)
    assert [t["role"] for t in res.turns] == ["user"]


def test_crash_mid_tmux_turn_surfaces_interrupted():
    # LOAD-BEARING: tmux crash mid-turn — terminal frames, NO result, then a question.
    rows = [
        _conv_turn(1, {"id": "u1", "role": "user", "content": "build it", "uuid": "U1"}),
        _entry(2, "user", {"uuid": "U1", "message": {"content": "build it"}}),
        _entry(3, "terminal_frame", {"rows": ["Razzle-dazzling the widget…", "↑ 31.3k tokens"]}),
        _entry(4, "terminal_frame", {"rows": ["Razzle-dazzling the widget…", "edited foo.py"]}),
        _entry(
            5, "ask_user_question", {"request_id": "q1", "questions": [{"question": "Proceed?"}]}
        ),
    ]
    res = rebuild_turns(rows)
    assert [t["role"] for t in res.turns] == ["user", "assistant"]
    asst = res.turns[1]
    assert asst["metadata"]["status"] == "interrupted"
    assert asst["metadata"].get("provenance") == "terminal_scrape"
    assert "edited foo.py" in asst["content"]
    assert res.partial is True


def test_crash_mid_turn_delta_path_prefers_deltas_over_scrape():
    rows = [
        _conv_turn(1, {"id": "u1", "role": "user", "content": "go", "uuid": "U1"}),
        _entry(2, "assistant", {"message": {"content": []}}),
        _entry(3, "content_block_delta", {"delta": {"type": "text_delta", "text": "Hello "}}),
        _entry(4, "content_block_delta", {"delta": {"type": "text_delta", "text": "world"}}),
        _entry(5, "terminal_frame", {"rows": ["garbled pane text"]}),
    ]
    res = rebuild_turns(rows)
    asst = res.turns[-1]
    assert asst["content"] == "Hello world"  # deltas win; scrape NOT used
    assert asst["metadata"].get("provenance") is None
    assert asst["metadata"]["status"] == "interrupted"


def test_tmux_completed_turn_via_result_not_interrupted():
    rows = [
        _conv_turn(1, {"id": "u1", "role": "user", "content": "go", "uuid": "U1"}),
        _entry(2, "content_block_delta", {"delta": {"text": "answer"}}),
        _entry(3, "result", {"result": "answer", "modelUsage": {}, "stop_reason": "end_turn"}),
    ]
    res = rebuild_turns(rows)
    asst = res.turns[-1]
    assert asst["role"] == "assistant"
    assert asst["content"] == "answer"
    assert "status" not in asst["metadata"]
    assert res.partial is False


def test_tool_result_only_user_enriches_assistant_no_user_turn():
    rows = [
        _entry(
            1,
            "assistant",
            {
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
                }
            },
        ),
        _entry(
            2,
            "user",
            {
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]
                }
            },
        ),
        _entry(3, "result", {"result": "done", "modelUsage": {}}),
    ]
    res = rebuild_turns(rows)
    assert [t["role"] for t in res.turns] == ["assistant"]
    part_types = [p["type"] for p in res.turns[0]["parts"]]
    assert "tool_use" in part_types and "tool_result" in part_types
    # The turn streamed NO assistant text (tool_use only), so the result frame's text is
    # injected as the turn content — matching what a live viewer saw (B-2 / INV-4 / FR-3).
    assert res.turns[0]["content"] == "done"


def test_error_frame_flushes_then_error_turn():
    rows = [
        _entry(1, "content_block_delta", {"delta": {"text": "partial"}}),
        _entry(2, "error", {"error": {"message": "boom"}}),
    ]
    res = rebuild_turns(rows)
    assert len(res.turns) == 2
    assert res.turns[0]["content"] == "partial"
    assert res.turns[1]["metadata"]["status"] == "error"
    assert res.partial is True


def test_ordering_by_outer_seq_not_payload_seq():
    rows = [
        _entry(3, "content_block_delta", {"delta": {"text": "C"}, "seq": 1}),
        _entry(1, "user", {"uuid": "U", "message": {"content": "Q"}, "seq": 99}),
        _entry(2, "content_block_delta", {"delta": {"text": "B"}, "seq": 50}),
        _entry(4, "result", {"result": "", "modelUsage": {}}),
    ]
    res = rebuild_turns(rows)
    assert [t["role"] for t in res.turns] == ["user", "assistant"]
    assert res.turns[1]["content"] == "BC"  # ordered by entry.seq (2 then 3), not payload seq


def test_idempotent_stable_ids():
    rows = [
        _entry(1, "user", {"uuid": "U", "message": {"content": "hi"}}),
        _entry(2, "content_block_delta", {"delta": {"text": "yo"}}),
        _entry(3, "result", {"result": "yo", "modelUsage": {}}),
    ]
    a = rebuild_turns(rows)
    b = rebuild_turns(list(reversed(rows)))  # same data, shuffled input
    assert [t["id"] for t in a.turns] == [t["id"] for t in b.turns]
    assert a.turns == b.turns


def test_empty_or_chrome_only_log_returns_no_turns():
    rows = [
        _entry(1, "system", {"foo": "bar"}),
        _entry(2, "init", {}),
        _entry(3, "available_commands", {"commands": []}),
    ]
    res = rebuild_turns(rows)
    assert res.turns == []
    assert res.partial is False


def test_empty_input_returns_empty():
    res = rebuild_turns([])
    assert res.turns == []
