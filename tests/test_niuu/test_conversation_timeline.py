"""Pure timeline contract: known observations only, immutable inputs, stable slices."""

import copy

import pytest

from niuu.domain.conversation_timeline import (
    TIMELINE_KEY,
    observation,
    project_timeline,
    stamp_parts,
    timeline,
)
from niuu.domain.text_projection import projection_revision


def _stamp(seq):
    return observation(seq, f"2026-09-14T07:30:{seq:02d}+00:00")


def _user(seq):
    return {
        "id": f"user-{seq}",
        "role": "user",
        "content": "same text",
        "parts": [],
        "created_at": _stamp(seq)["observed_at"],
        "metadata": {TIMELINE_KEY: _stamp(seq), "request_id": f"request-{seq}"},
    }


def _part(seq, **kwargs):
    return {
        "type": "text",
        "id": f"item-{seq}",
        "text": f"text-{seq}",
        TIMELINE_KEY: {**_stamp(seq), "span_seq": 2},
        **kwargs,
    }


def _assistant(parts):
    return {
        "id": "in-progress",
        "role": "assistant",
        "content": "aggregate",
        "parts": parts,
        "metadata": {"status": "in_progress"},
        "in_progress": True,
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {TIMELINE_KEY: []},
        {TIMELINE_KEY: {"schema": 2}},
        {TIMELINE_KEY: {**_stamp(1), "seq": True}},
        {TIMELINE_KEY: {**_stamp(1), "seq": 0}},
        {TIMELINE_KEY: {**_stamp(1), "observed_at": ""}},
        {TIMELINE_KEY: {**_stamp(1), "observed_at": 12}},
        {TIMELINE_KEY: {**_stamp(1), "observed_at": "2026-09-14"}},
        {TIMELINE_KEY: {**_stamp(1), "observed_at": "unknown"}},
    ],
)
def test_unknown_observations_are_not_invented(value):
    assert timeline(value) is None


def test_stamps_only_new_parts_and_preserves_original_position_on_completion():
    old = {"type": "text", "text": "untimed old history"}
    parts = [old, {"type": "text", "id": "new", "text": ""}]
    stamp_parts(parts, {TIMELINE_KEY: _stamp(2)}, start=1)
    assert parts[0] == old and TIMELINE_KEY not in old
    original = copy.deepcopy(parts[1][TIMELINE_KEY])
    parts[1]["text"] = "complete later"
    parts.append({"type": "tool_use", "id": "tool", "input": {}})
    stamp_parts(parts, {TIMELINE_KEY: _stamp(9)}, start=2)
    assert parts[1][TIMELINE_KEY] == original
    assert parts[2][TIMELINE_KEY] == {**_stamp(9), "span_seq": 2}
    stamp_parts(parts, {})
    assert parts[1][TIMELINE_KEY] == original


def test_duplicate_text_inputs_remain_distinct_and_projection_is_pure():
    rows = [_user(1), _user(5), _user(8), _assistant([_part(2), _part(6), _part(9)])]
    original = copy.deepcopy(rows)
    out = project_timeline(rows, "session")
    assert [r["id"] for r in out if r["role"] == "user"] == ["user-1", "user-5", "user-8"]
    assert [r["role"] for r in out] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert rows == original
    assert out == project_timeline(out, "session")
    assert out == project_timeline(rows, "session")
    assert len({r["id"] for r in out}) == 6
    assert project_timeline(rows, "another-session")[1]["id"] != out[1]["id"]


def test_legacy_rows_and_partial_timing_are_barriers_not_guessed_order():
    unknown = {"id": "old-user", "role": "user", "content": "old", "metadata": {}}
    partial = _assistant([_part(2), {"type": "text", "text": "untimed"}])
    rows = [_user(5), unknown, partial]
    assert project_timeline(rows, "session") == rows
    assert project_timeline([], "session") == []


def test_late_tool_output_stays_with_call_and_error_usage_only_on_last_fragment():
    parts = [
        _part(2, type="tool_use", id="call", input={"command": "run"}),
        _part(6),
        _part(9, type="tool_result", tool_use_id="call", content="done", is_error=False),
        _part(10, type="tool_result", tool_use_id="unknown", content="orphan", is_error=True),
    ]
    source = _assistant(parts)
    source["metadata"] = {
        "usage": {"model": {}},
        "cost": 1,
        "status": "error",
        "messageType": "error",
        "is_error": True,
        "error": "failed",
    }
    result = project_timeline([_user(5), source], "session")
    assert result[0]["parts"] == [parts[0], parts[2]]
    assert result[2]["parts"] == [parts[1], parts[3]]
    assert "usage" not in result[0]["metadata"] and "messageType" not in result[0]["metadata"]
    assert result[2]["metadata"]["usage"] == {"model": {}}


def test_same_span_id_when_empty_start_disappears_or_native_result_id_changes():
    before = [_user(1), _assistant([_part(2, text=""), _part(3)])]
    after = [_user(1), {**before[1], "id": "completed-native-parent", "parts": [_part(3)]}]
    assert (
        project_timeline(before, "session")[1]["id"] == project_timeline(after, "session")[1]["id"]
    )
    assert projection_revision(before) == projection_revision(after) == "text-items-1:0;timeline-1"
    assert projection_revision([{"role": "user", "content": "legacy"}]) == "text-items-1:0"


def test_pre_stamped_part_and_unmatched_malformed_result_keep_observation():
    parts = [_part(2), _part(6, type="tool_result", tool_use_id=[], content="output")]
    before = copy.deepcopy(parts)
    stamp_parts(parts, {TIMELINE_KEY: _stamp(9)})
    assert parts == before
    result = project_timeline([_user(5), _assistant(parts)], "session")
    assert [x["parts"] for x in result] == [[parts[0]], [], [parts[1]]]
