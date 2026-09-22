"""Inbox state and final-output classification stay independent from execution state."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from niuu.domain.transcript_reducer import result_metadata
from volundr.domain.session_read_state import (
    SessionReadState,
    SessionReadStateChange,
    is_final_output,
)


def final_frame():
    return {
        "type": "conversation.turn",
        "turn": {
            "id": "final-1",
            "role": "assistant",
            "visibility": "public",
            "content": "The answer",
            "metadata": {"final_output": True},
        },
    }


@pytest.mark.parametrize(
    "latest,through,forced,unread",
    [
        (0, 0, False, False),
        (10, 0, False, True),
        (10, 10, False, False),
        (11, 10, False, True),
        (10, 10, True, True),
        (0, 0, True, True),
    ],
)
def test_read_projection(latest, through, forced, unread):
    state = SessionReadState(
        latest_output_seq=latest, read_through_seq=through, manually_unread=forced
    )
    assert state.is_unread is unread
    assert state.model_dump()["is_unread"] is unread


def test_successful_result_stamps_final():
    assert result_metadata({"type": "result", "subtype": "success"})["final_output"] is True


@pytest.mark.parametrize(
    "payload",
    [{"is_error": True}, {"subtype": "error_during_execution"}, {"stop_reason": "interrupted"}],
)
def test_unsuccessful_results_do_not_stamp_final(payload):
    assert not result_metadata(payload).get("final_output", False)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda f: f.update(type="assistant"),
        lambda f: f.update(turn=None),
        lambda f: f["turn"].update(role="user"),
        lambda f: f["turn"].update(visibility="internal"),
        lambda f: f["turn"].update(metadata=[]),
        lambda f: f["turn"].update(metadata="bad"),
        lambda f: f["turn"].update(metadata={"final_output": "true"}),
        lambda f: f["turn"]["metadata"].update(status="interrupted"),
        lambda f: f["turn"]["metadata"].update(status="error"),
        lambda f: f["turn"]["metadata"].update(is_error=True),
        lambda f: f["turn"].update(content=" \n"),
        lambda f: f["turn"].update(content=[{"type": "tool_use"}]),
        lambda f: f["turn"].update(id=""),
    ],
)
def test_only_public_nonempty_final_text_creates_unread(mutation):
    frame = deepcopy(final_frame())
    assert is_final_output(frame)
    mutation(frame)
    assert not is_final_output(frame)


@pytest.mark.parametrize(
    "change",
    [
        {"state": "read"},
        {"state": "invalid", "expected_revision": 0, "through_seq": 0},
        {"state": "read", "expected_revision": -1, "through_seq": 0},
        {"state": "read", "expected_revision": 0, "through_seq": -1},
    ],
)
def test_invalid_mutations_are_rejected(change):
    with pytest.raises(ValidationError):
        SessionReadStateChange(**change)
