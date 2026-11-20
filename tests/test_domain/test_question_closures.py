"""LEX-1979: a closed question stays closed in a rebuild, whatever turn it is in."""

from __future__ import annotations

from types import SimpleNamespace

from niuu.domain.transcript_rebuild import rebuild_turns
from niuu.domain.transcript_reducer import (
    QUESTION_CLOSURE_SOURCE,
    apply_question_closures,
    reduce_frames,
)
from skuld.history_hydration import merge_history_turns

PHANTOM = "toolu_01FNSi1eEKu7LVLCvQ9qL51y"


def _row(seq: int, kind: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session", seq=seq, kind=kind, ts=None, request_id=None, payload=payload
    )


def _question_turn() -> dict:
    return {
        "id": "turn-6e42a22c",
        "role": "assistant",
        "content": "",
        "parts": [
            {"type": "text", "text": "One question first."},
            {"type": "tool_use", "id": PHANTOM, "name": "AskUserQuestion", "input": {}},
        ],
        "created_at": "2026-10-19T07:15:11+00:00",
        "metadata": {},
    }


def _dismissed(seq: int, **extra) -> SimpleNamespace:
    return _row(
        seq,
        "ask_user_resolved",
        {
            "type": "ask_user_resolved",
            "request_id": "reconcile:" + PHANTOM,
            "tool_use_id": PHANTOM,
            "dismissed": True,
            "dismiss_reason": "never_shown",
            "dismiss_detail": "the CLI never showed it (its call is not in the session file)",
            "control_kind": "question",
            **extra,
        },
    )


def _results(turn: dict) -> list[dict]:
    return [part for part in turn["parts"] if part["type"] == "tool_result"]


def test_a_rebuild_closes_a_question_in_a_finished_turn():
    """The seed is the stored turn; the close came weeks later, after other turns."""
    seed = _row(10, "conversation.turn", {"turn": _question_turn()})
    later = _row(
        11, "conversation.turn", {"turn": {**_question_turn(), "id": "turn-2", "parts": []}}
    )
    result = rebuild_turns([seed, later, _dismissed(500)])
    [closure] = _results(result.turns[0])
    assert closure["tool_use_id"] == PHANTOM
    assert closure["is_error"] is True
    assert closure["content"].startswith("Dismissed: the CLI never showed it")
    assert closure["metadata"]["source"] == QUESTION_CLOSURE_SOURCE
    assert closure["metadata"]["dismiss_reason"] == "never_shown"


def test_the_overlay_never_mutates_the_stored_turn_and_is_idempotent():
    turn = _question_turn()
    once = apply_question_closures([turn], [_dismissed(5)])
    twice = apply_question_closures(once, [_dismissed(5), _dismissed(6)])
    assert _results(turn) == []
    assert len(_results(once[0])) == 1 and len(_results(twice[0])) == 1


def test_a_real_result_anywhere_wins_over_a_closure():
    turn = _question_turn()
    answered = {
        "id": "t2",
        "role": "assistant",
        "parts": [{"type": "tool_result", "tool_use_id": PHANTOM, "content": "Yes"}],
    }
    result = apply_question_closures([turn, answered], [_dismissed(5)])
    assert _results(result[0]) == []


def test_an_answer_read_from_the_session_file_closes_with_that_answer():
    result = apply_question_closures(
        [_question_turn()], [_dismissed(5, native_result_content="User answered: Yes")]
    )
    [closure] = _results(result[0])
    assert (closure["content"], closure["is_error"]) == ("User answered: Yes", False)


def test_only_dismissed_question_closes_count():
    answered = _dismissed(5)
    answered.payload = {**answered.payload, "dismissed": False}
    permission = _dismissed(6)
    permission.payload = {**permission.payload, "control_kind": "permission"}
    assert apply_question_closures([_question_turn()], [answered, permission]) == [_question_turn()]


def test_the_live_reduction_applies_the_same_closure():
    rows = [
        _row(
            1,
            "assistant",
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": PHANTOM, "name": "AskUserQuestion", "input": {}}
                    ]
                },
            },
        ),
        _dismissed(2),
        _row(3, "result", {"type": "result", "result": "done"}),
    ]
    [turn] = reduce_frames(rows).turns
    assert [p["tool_use_id"] for p in _results(turn)] == [PHANTOM]


def test_a_restart_never_brings_an_orphan_back():
    """Hydration keeps the closed turn whether the cache or the durable rebuild has it."""
    closed = apply_question_closures([_question_turn()], [_dismissed(5)])[0]
    stale_cache = _question_turn()
    assert _results(merge_history_turns([closed], [stale_cache])[0])
    assert _results(merge_history_turns([_question_turn()], [closed])[0])


def _assistant(seq: int, *blocks: dict) -> SimpleNamespace:
    return _row(seq, "assistant", {"type": "assistant", "message": {"content": list(blocks)}})


QUESTION_USE = {"type": "tool_use", "id": PHANTOM, "name": "AskUserQuestion", "input": {}}


def test_the_closure_sits_where_the_live_broker_put_it():
    """Codex review: live appends the closure when the frame arrives, before later text."""
    rows = [
        _assistant(1, QUESTION_USE),
        _dismissed(2),
        _assistant(3, {"type": "text", "text": "Moving on."}),
        _row(4, "result", {"type": "result", "result": "Moving on."}),
    ]
    [turn] = reduce_frames(rows).turns
    assert [p["type"] for p in turn["parts"]][:3] == ["tool_use", "tool_result", "text"]


def test_a_real_answer_after_a_close_replaces_the_closure_on_rebuild():
    rows = [
        _assistant(1, QUESTION_USE),
        _dismissed(2),
        _row(
            3,
            "user",
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": PHANTOM, "content": "Yes"}]
                },
            },
        ),
        _row(4, "result", {"type": "result", "result": "ok"}),
    ]
    [turn] = reduce_frames(rows).turns
    assert [(p["content"], p.get("metadata")) for p in _results(turn)] == [("Yes", None)]


def test_a_timestamped_closure_still_merges_over_a_stale_cache():
    """Codex review: stamping the tool_use broke the prefix and kept the stale cache."""
    seed = _row(10, "conversation.turn", {"turn": _question_turn()})
    dismissed = _dismissed(20)
    dismissed.ts = "2026-11-08T09:30:00+00:00"
    closed = rebuild_turns([seed, dismissed]).turns[0]
    assert "ended_at" not in closed["parts"][1]
    merged = merge_history_turns([closed], [_question_turn()])
    assert _results(merged[0])[0]["ended_at"] == "2026-11-08T09:30:00+00:00"


def test_a_rebuild_never_mutates_the_seed_it_was_given():
    turn = _question_turn()
    reduce_frames([_dismissed(2)], sdk_turns=[turn])
    assert _results(turn) == []


def test_a_seeded_rebuild_drops_a_closure_whose_real_answer_is_in_another_seed():
    """Codex re-check: two stored turns, the closure in one and the real answer in the next."""
    closed = apply_question_closures([_question_turn()], [_dismissed(5)])[0]
    answer = {
        "id": "turn-2",
        "role": "assistant",
        "content": "",
        "parts": [{"type": "tool_result", "tool_use_id": PHANTOM, "content": "Yes"}],
        "created_at": "2026-10-19T07:20:00+00:00",
        "metadata": {},
    }
    rows = [
        _row(10, "conversation.turn", {"turn": closed}),
        _row(11, "conversation.turn", {"turn": answer}),
    ]
    turns = rebuild_turns(rows).turns
    assert [p["content"] for t in turns for p in _results(t)] == ["Yes"]
    assert _results(closed)  # the stored seed itself is untouched


def test_a_real_answer_after_a_closure_keeps_its_timeline_stamp():
    """Codex re-check: dropping mid-reduction shifted the index the result is stamped from."""
    from niuu.domain.conversation_timeline import TIMELINE_KEY, TIMELINE_SCHEMA

    def stamp(seq: int) -> dict:
        return {
            TIMELINE_KEY: {
                "schema": TIMELINE_SCHEMA,
                "seq": seq,
                "observed_at": f"2026-11-08T09:3{seq}:00+00:00",
            }
        }

    answer = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": PHANTOM, "content": "Yes"}]},
    }
    rows = [
        _row(
            1,
            "assistant",
            {"type": "assistant", "message": {"content": [QUESTION_USE]}, **stamp(1)},
        ),
        _dismissed(2),
        _row(3, "user", {**answer, **stamp(3)}),
        _row(4, "result", {"type": "result", "result": "ok", **stamp(4)}),
    ]
    [turn] = reduce_frames(rows).turns
    [result] = _results(turn)
    assert result["content"] == "Yes"
    assert result[TIMELINE_KEY]["seq"] == 3
