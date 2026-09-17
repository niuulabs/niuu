"""An idle tmux paint cannot reopen a completed public replay turn."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from volundr.domain.models import SessionLogEntry
from volundr.domain.services.transcript_rebuild import rebuild_turns

SID = UUID("bf1b7317-0af3-4823-af08-930d0fe8dc22")


def row(seq, kind, **payload):
    return SessionLogEntry(
        session_id=SID,
        seq=seq,
        kind=kind,
        payload={"type": kind, **payload},
        ts=datetime(2026, 9, 8, 22, 21, seq, tzinfo=UTC),
    )


def completed(*, terminal=None):
    return [
        row(1, "user", uuid="request-a", message={"content": "Run the test."}),
        row(2, "assistant", message={"content": [{"type": "text", "text": "Done café 東京."}]}),
        row(3, "result", result="Done café 東京.", **(terminal or {})),
    ]


@pytest.mark.parametrize("kind", ["terminal_frame", "terminal_snapshot"])
@pytest.mark.parametrize("terminal", [{}, {"is_error": True}, {"stop_reason": "cancelled"}])
def test_idle_terminal_paint_after_result_cannot_create_phantom_interrupted_turn(kind, terminal):
    # Real Claude sequence: result318, internal seed319 filtered from public, paint320.
    frames = completed(terminal=terminal)
    expected = rebuild_turns(frames)
    actual = rebuild_turns(
        [
            *frames,
            row(4, kind, rows=["Old prompt and every earlier assistant answer."]),
            row(5, kind, rows=["Redrawn entire scrollback."]),
        ]
    )
    assert actual == expected
    assert len(actual.turns) == 2


@pytest.mark.parametrize("with_user", [False, True])
def test_genuine_terminal_only_crash_history_remains_available(with_user):
    frames = (
        [row(1, "user", uuid="request-a", message={"content": "Run the test."})]
        if with_user
        else []
    )
    frames.append(row(2, "terminal_frame", rows=["The raw-only agent was still working."]))
    result = rebuild_turns(frames)
    assert result.partial is True
    assert result.turns[-1]["content"] == "The raw-only agent was still working."
    assert result.turns[-1]["metadata"] == {
        "provenance": "terminal_scrape",
        "status": "interrupted",
    }


@pytest.mark.parametrize("kind", ["user", "user_confirmed"])
def test_new_human_turn_reenables_terminal_only_fallback_after_completion(kind):
    payload = (
        {"uuid": "request-b", "message": {"content": "Next task."}}
        if kind == "user"
        else {"id": "request-b", "content": "Next task."}
    )
    result = rebuild_turns(
        [
            *completed(),
            row(4, "terminal_snapshot", rows=["Idle old screen must be ignored."]),
            row(5, kind, **payload),
            row(6, "terminal_frame", rows=["The next task has unfinished pane output."]),
        ]
    )
    assert [turn["role"] for turn in result.turns] == ["user", "assistant", "user", "assistant"]
    assert result.turns[-1]["content"] == "The next task has unfinished pane output."
    assert result.turns[-1]["metadata"]["status"] == "interrupted"
    assert result.partial is True


def test_duplicate_human_confirmation_cannot_reopen_idle_pane_fallback():
    frames = [
        *completed(),
        row(4, "user_confirmed", id="request-a", content="Run the test."),
    ]
    expected = rebuild_turns(frames)
    actual = rebuild_turns([*frames, row(5, "terminal_frame", rows=["Old scrollback."])])
    assert actual == expected


def test_explicit_next_assistant_start_keeps_raw_only_fallback_available():
    result = rebuild_turns(
        [
            *completed(),
            row(4, "assistant", turn_id="new-native-turn", message={"content": []}),
            row(5, "terminal_frame", rows=["New native turn pane output."]),
        ]
    )
    assert len(result.turns) == 3
    assert result.turns[-1]["content"] == "New native turn pane output."
    assert result.partial is True
