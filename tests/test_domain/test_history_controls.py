"""Retain raw control evidence without fabricating an assistant error/terminal."""

import copy

import pytest

from niuu.domain.transcript_reducer import is_read_path_excluded, reduce_frames
from tests.test_domain.test_transcript_rebuild import _entry
from volundr.domain.services.transcript_rebuild import rebuild_turns


@pytest.mark.parametrize(
    "control",
    [
        {"type": "error", "code": "conversation_history_too_large", "content": "synthetic warning"},
        {"type": "history_gap", "reason": "snapshot_too_large", "recovery": "recent"},
        {"type": "error", "content": "viewer-specific", "_per_connect_handshake": True},
    ],
)
def test_history_control_never_flushes_work_or_changes_replay(control):
    before = _entry(1, "assistant", {"message": {"content": [{"type": "text", "text": "before"}]}})
    after = _entry(3, "assistant", {"message": {"content": [{"type": "text", "text": "after"}]}})
    end = _entry(4, "result", {"subtype": "success"})
    rows = [before, _entry(2, control["type"], control), after, end]
    original = copy.deepcopy(rows)
    assert is_read_path_excluded(control["type"], control)
    for reducer in (reduce_frames, rebuild_turns):
        result = reducer(rows)
        assert result == reducer([before, after, end])
        assert not result.partial
    assert rows == original, "Raw evidence must not be rewritten/deleted"


def test_similar_prose_and_real_error_remain_visible():
    phrase = "Conversation history is too large for WebSocket replay. Reload history through REST."
    rows = [
        _entry(1, "assistant", {"message": {"content": [{"type": "text", "text": phrase}]}}),
        _entry(2, "error", {"content": phrase, "code": "provider_failure"}),
    ]
    assert not is_read_path_excluded("error", rows[1].payload)
    result = reduce_frames(rows)
    assert len(result.turns) == 2
    assert all(turn["content"] == phrase for turn in result.turns)
    assert result.turns[-1]["metadata"]["status"] == "error"
