"""Protocol2 paging: stable seams, growth continuations and immutable originals."""

import base64
import copy
import json
from pathlib import Path

import pytest

from niuu.domain.history_paging import InvalidHistoryCursorError
from skuld.conversation_snapshot import (
    ConversationSnapshotTooLargeError,
    prepare_history_page,
    snapshot_byte_size,
)


def rows(count=30, content="synthetic"):
    return [
        {"id": str(i), "role": "assistant", "content": content, "parts": []} for i in range(count)
    ]


def page(
    turns, cursor=None, *, limit=5, budget=8192, sid="session", revision=None, part_paging=False
):
    frame = {"turns": turns}
    if revision:
        frame["projection_revision"] = revision
    return prepare_history_page(
        frame,
        session_id=sid,
        max_turns=limit,
        max_bytes=budget,
        cursor=cursor,
        part_paging=part_paging,
    )


def tool_turn(calls, *, turn_id="long", result_bytes=600, content="final answer"):
    parts = []
    for i in range(calls):
        parts.append({"type": "text", "text": f"step {i}"})
        parts.append(
            {"type": "tool_use", "id": f"c{i}", "name": "Bash", "input": {"command": "ls"}}
        )
        parts.append({"type": "tool_result", "tool_use_id": f"c{i}", "content": "r" * result_bytes})
    return {"id": turn_id, "role": "assistant", "content": content, "parts": parts}


def no_preview(result):
    return not any("history_preview" in item for item in (result, *result["turns"]))


def load_turn_by_parts(source, *, budget=8192, limit=5):
    """Walk part_paging pages newest-first; return every page and the turns rebuilt."""
    pages, rows = [], {}
    current = page(source, limit=limit, budget=budget, part_paging=True)
    while True:
        pages.append(current)
        for turn in reversed(current["turns"]):
            held = rows.setdefault(turn["id"], {**turn, "parts": []})
            held["parts"] = turn["parts"] + held["parts"]
            held.update({k: v for k, v in turn.items() if k not in ("parts", "part_window")})
            held.pop("part_window", None)
        if not current["older_cursor"]:
            break
        current = page(
            source, current["older_cursor"], limit=limit, budget=budget, part_paging=True
        )
    return pages, [rows[turn["id"]] for turn in source if turn["id"] in rows]


def test_older_cursor_does_not_move_when_new_turns_arrive():
    source = rows()
    first = page(source)
    source.extend(rows(3))  # even duplicate IDs AFTER the seam cannot move it
    older = page(source, first["older_cursor"])
    assert [t["id"] for t in older["turns"]] == list(map(str, range(20, 25)))
    assert older["window_offset"] == 20 and older["window_end"] == 25
    assert older["total_turns"] == 33
    assert older["page_kind"] == "older"


def test_refresh_keeps_full_fixed_interval_across_growth_with_exact_continuations():
    source = rows(8)
    first = page(source, limit=8)
    for turn in source:
        turn["content"] = "界😀\n" * 230
        turn["metadata"] = {"status": "completed"}
    expected = copy.deepcopy(source)
    source.extend(rows(2, content="new unrelated rows"))
    cursor = first["refresh_cursor"]
    received = []
    while cursor:
        current = page(source, cursor, limit=8)
        assert snapshot_byte_size(current) <= 8192
        assert current["page_kind"] == "refresh"
        assert current["window_offset"] + len(current["turns"]) == current["window_end"]
        received = current["turns"] + received
        cursor = current["continuation_cursor"]
        assert current["page_complete"] is (cursor is None)
    assert received == expected
    assert len({t["id"] for t in received}) == 8


@pytest.mark.parametrize("change", ["repair", "reorder", "remove", "session"])
def test_cursor_rejects_changed_projection_not_normal_content(change):
    source = rows()
    first = page(source)
    kwargs = {}
    if change == "repair":
        kwargs["revision"] = "repaired"
    elif change == "reorder":
        source[0], source[1] = source[1], source[0]
    elif change == "remove":
        del source[:20]
    else:
        kwargs["sid"] = "different-session"
    with pytest.raises(InvalidHistoryCursorError):
        page(source, first["older_cursor"], **kwargs)


@pytest.mark.parametrize("cursor", ["%%%", "W10=", "e30=", "a" * 5000])
def test_malformed_cursor_is_not_ignored(cursor):
    with pytest.raises(ValueError):
        page(rows(), cursor)


@pytest.mark.parametrize("huge", ["prose", "metadata", "tools", "control_chars"])
@pytest.mark.parametrize("part_paging", [False, True])
def test_one_huge_item_is_served_whole_never_cut_or_flagged(huge, part_paging):
    source = rows(3)
    content = "x" * 150_000 if huge != "control_chars" else "\0" * 150_000
    big = source[-1]
    if huge == "metadata":
        big["metadata"] = {"evidence": content}
        big["visibility"] = "internal"
        big["participant_id"] = "synthetic-internal-sender"
    elif huge == "tools":
        big["parts"] = [{"type": "tool_use", "id": "only", "name": "Bash", "input": {}}]
        big["content"] = content
    else:
        big["content"] = content
    original = copy.deepcopy(source)
    result = page(source, part_paging=part_paging)
    # One atom (the turn's envelope, or its single part) bigger than the budget:
    # it is delivered whole, over the budget, rather than cut.
    assert snapshot_byte_size(result) > 8192
    assert result["turns"] == [big]
    assert no_preview(result)
    assert source == original
    assert result["window_offset"] == 2 and result["total_turns"] == 3
    assert result["has_more_before"] and result["older_cursor"]


def test_overnight_turn_comes_back_whole_with_every_tool_call():
    """LEX-1937 regression: one ~1 h 40 min turn with hundreds of tool calls."""
    source = [*rows(4, content="earlier work " * 50), tool_turn(700, result_bytes=5000)]
    original = copy.deepcopy(source)
    result = page(source, budget=256 * 1024, limit=15)
    turn = result["turns"][-1]
    assert [t["id"] for t in result["turns"]] == ["long"]
    assert result["window_offset"] == 4
    assert snapshot_byte_size(result) > 256 * 1024  # bigger than the budget, still whole
    assert len([p for p in turn["parts"] if p["type"] == "tool_use"]) == 700
    assert len([p for p in turn["parts"] if p["type"] == "tool_result"]) == 700
    assert len(turn["parts"]) == len(source[-1]["parts"])
    assert turn["content"] == "final answer"
    assert no_preview(result) and "part_window" not in turn
    # Shallow elision is unchanged: heavy results stay lazy references.
    lazy = [p for p in turn["parts"] if p["type"] == "tool_result"]
    assert all(p["truncated"] and p["byte_size"] == 5000 and "content" not in p for p in lazy)
    assert source == original


def test_budget_drops_only_older_whole_turns():
    source = [
        {"id": str(i), "role": "assistant", "content": f"{i} " + "x" * 1500, "parts": []}
        for i in range(12)
    ]
    first = page(source, limit=10, budget=8192)
    assert snapshot_byte_size(first) <= 8192
    assert 1 < len(first["turns"]) < 10
    assert first["turns"] == source[first["window_offset"] :]
    assert not first["page_complete"] and no_preview(first)
    older = page(source, first["older_cursor"], limit=10, budget=8192)
    assert older["window_end"] == first["window_offset"]
    assert older["turns"] == source[older["window_offset"] : older["window_end"]]


def test_part_paging_pages_within_one_huge_turn_with_complete_parts():
    source = [*rows(3), tool_turn(120)]
    original = copy.deepcopy(source)
    pages, rebuilt = load_turn_by_parts(source)
    assert len(pages) > 2
    for current in pages:
        assert snapshot_byte_size(current) <= 8192
        assert current["window_offset"] + len(current["turns"]) == current["window_end"]
        assert no_preview(current)
    newest = pages[0]["turns"][0]
    window = newest["part_window"]
    assert pages[0]["window_offset"] == 3 and pages[0]["window_end"] == 4
    assert window["end"] == window["total"] == 360 and window["offset"] > 0
    assert newest["content"] == "final answer"
    # Older fragments carry identity and metadata, not the whole-turn text again.
    middle = pages[1]["turns"][-1]
    assert "content" not in middle and middle["part_window"]["end"] == window["offset"]
    # Each page starts on a part whose tool call (if any) it also carries.
    for current in pages:
        parts = current["turns"][-1]["parts"]
        calls = {p["id"] for p in parts if p["type"] == "tool_use"}
        assert all(p["tool_use_id"] in calls for p in parts if p["type"] == "tool_result")
    # The head fragment shares its page with the earlier whole turns that fit.
    assert pages[-1]["window_offset"] == 0 and not pages[-1]["has_more_before"]
    assert rebuilt == source
    assert source == original


def test_part_paging_refresh_returns_the_same_fragment_and_continues_after_growth():
    source = [tool_turn(80)]
    first = page(source, part_paging=True)
    window = first["turns"][0]["part_window"]
    again = page(source, first["refresh_cursor"])
    assert again["turns"] == first["turns"]
    assert again["page_kind"] == "refresh" and again["page_complete"]
    # A refresh of the whole (formerly small) turn after it grew past the budget
    # serves its newest complete parts and continues with the rest of the turn.
    small = [tool_turn(2)]
    whole = page(small)
    small[0]["parts"].extend(tool_turn(80)["parts"][6:])
    current, received = page(small, whole["refresh_cursor"], part_paging=True), []
    assert not current["page_complete"] and current["continuation_cursor"]
    while True:
        received = current["turns"][0]["parts"] + received
        if not current["continuation_cursor"]:
            break
        current = page(small, current["continuation_cursor"])
    assert received == small[0]["parts"]
    assert window["total"] == 240


def test_part_cursor_rejects_a_rewritten_turn_and_malformed_bounds():
    source = [tool_turn(80)]
    first = page(source, part_paging=True)
    shorter = [tool_turn(10)]
    with pytest.raises(InvalidHistoryCursorError):
        page(shorter, first["older_cursor"])
    for bounds in ([3, 3], [5, 2], [-1, 4], ["0", 4], [2, 9]):
        start = -1 if bounds == [2, 9] else 0
        payload = [2, "session", first["projection_revision"], start, 1, "x", *bounds]
        cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        with pytest.raises(ValueError):
            page(source, cursor)


def test_part_paging_a_parallel_tool_group_never_starts_on_an_orphan_result():
    parts = [{"type": "tool_use", "id": f"p{i}", "name": "Read", "input": {}} for i in range(40)]
    parts += [
        {"type": "tool_result", "tool_use_id": f"p{i}", "content": "r" * 300} for i in range(40)
    ]
    source = [{"id": "group", "role": "assistant", "content": "", "parts": parts}]
    result = page(source, part_paging=True)
    # No valid boundary inside the group: it is served whole rather than split.
    assert result["turns"][0]["parts"] == parts
    assert "part_window" not in result["turns"][0]
    assert not result["has_more_before"]


def test_single_large_tool_result_preserves_call_and_lazy_result_without_item_preview():
    source = rows(1)
    source[0]["parts"] = [
        {"type": "tool_use", "id": "c", "name": "Bash", "input": {"command": "synthetic"}},
        {"type": "tool_result", "tool_use_id": "c", "content": "x" * 2_000_000},
    ]
    result = page(source)
    assert not result.get("history_preview")
    call, output = result["turns"][0]["parts"]
    assert call["id"] == output["tool_use_id"] == "c"
    assert output["truncated"] is True and output["byte_size"] == 2_000_000
    assert "content" not in output


def test_unselected_payload_is_never_json_encoded_or_deepcopied():
    class Unserializable:
        def __deepcopy__(self, memo):
            raise AssertionError("copied old tool payload")

    source = rows(10000)
    source[0]["parts"] = [{"type": "tool_result", "content": Unserializable()}]
    result = page(source)
    assert len(result["turns"]) == 5 and result["window_offset"] == 9995


def test_empty_history_is_explicit_complete_empty_window():
    result = page([])
    assert result["turns"] == []
    assert result["page_complete"] and not result["has_more_before"]
    assert result["older_cursor"] is result["refresh_cursor"] is None


def test_too_small_budget_is_explicit_failure():
    with pytest.raises(ConversationSnapshotTooLargeError):
        page(rows(), budget=1024)


def test_one_huge_part_array_pages_without_quadratic_serialization(monkeypatch):
    from skuld import conversation_snapshot as module

    actual = module.snapshot_byte_size
    encoded = []

    def measured(value):
        size = actual(value)
        encoded.append(size)
        return size

    source = rows(1)
    source[0]["parts"] = [{"type": "text", "text": "synthetic" * 20} for _ in range(10_000)]
    whole = actual(source[0])
    monkeypatch.setattr(module, "snapshot_byte_size", measured)
    result = page(source, part_paging=True)
    assert result["turns"][0]["part_window"]["end"] == 10_000
    assert sum(encoded) < 3 * whole


def test_native_wire_fixture_has_complete_disjoint_refresh_continuations():
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/history_protocol_v2.json").read_text()
    )
    received = []
    for current in fixture["grown_refresh_pages"]:
        assert current["history_protocol"] == 2
        assert snapshot_byte_size(current) <= 8192
        assert current["page_complete"] is (current["continuation_cursor"] is None)
        received = [turn["id"] for turn in current["turns"]] + received
    assert received == fixture["expected_refreshed_ids"]
    source = [
        {
            "id": f"row-{i}",
            "role": "assistant",
            "content": f"Synthetic text {i}",
            "parts": [],
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        for i in range(8)
    ]
    actual = prepare_history_page(
        {"turns": source, "head_seq": 321, "history_source": "gateway"},
        session_id=fixture["session_id"],
        max_bytes=8192,
        max_turns=4,
    )
    assert actual == fixture["initial"]
    metadata_row = fixture["metadata_preview"]["turns"][0]
    assert metadata_row["history_metadata_preview"] is True
    assert metadata_row["history_ref"] == {"turn_id": "metadata-preview-row"}
    assert metadata_row["role"] == "user"
    assert metadata_row["visibility"] == "internal"
    assert metadata_row["participant_id"] == "synthetic-internal-sender"


@pytest.mark.parametrize("source", [[{"content": "missing identity"}], [{"id": ""}]])
def test_missing_stable_identity_cannot_mint_a_successful_cursor(source):
    with pytest.raises(InvalidHistoryCursorError):
        page(source)


def test_valid_base64_with_invalid_cursor_schema_is_rejected():
    import base64

    cursor = base64.urlsafe_b64encode(
        json.dumps([2, "session", "rev", False, 1, "x"]).encode()
    ).decode()
    with pytest.raises(ValueError, match="Malformed"):
        page(rows(), cursor)


def _kinds(turn):
    return [(p.get("type"), p.get("id") or p.get("tool_use_id")) for p in turn["parts"]]


def test_an_oversize_turn_is_elided_deeper_and_fits_without_part_paging():
    """LEX-2017: one turn of 300 calls with ~600 B results (each under the shallow
    1 KB line) was served whole over budget, and the apps reject such a page, so
    "Show earlier" stopped there. Every part and call stays; only bodies go lazy."""
    source = [*rows(4), tool_turn(300, result_bytes=600)]
    original = copy.deepcopy(source)
    shallow_only = snapshot_byte_size(page(source, budget=10**9, limit=1))
    budget = 128 * 1024
    assert shallow_only > budget  # the old page would not fit

    result = page(source, budget=budget, limit=15)

    assert snapshot_byte_size(result) <= budget
    [turn] = result["turns"]
    assert turn["id"] == "long" and "part_window" not in turn
    assert _kinds(turn) == _kinds(source[-1])  # every part, every call, in order
    assert turn["content"] == "final answer"
    lazy = [p for p in turn["parts"] if p["type"] == "tool_result"]
    assert all(p["truncated"] and p["byte_size"] == 600 and "content" not in p for p in lazy)
    assert result["has_more_before"] and result["older_cursor"]
    assert source == original


def test_part_paging_clients_still_page_within_the_turn_unelided():
    source = [*rows(4), tool_turn(300, result_bytes=600)]
    result = page(source, budget=128 * 1024, limit=15, part_paging=True)
    [turn] = result["turns"]
    assert turn["part_window"]["end"] == len(source[-1]["parts"])
    assert turn["part_window"]["offset"] > 0
    results = [p for p in turn["parts"] if p["type"] == "tool_result"]
    assert results and all(p["content"] == "r" * 600 for p in results)


def test_a_turn_too_big_even_deep_elided_is_served_whole_as_before():
    source = [*rows(2), tool_turn(300, result_bytes=600, content="y" * 200_000)]
    result = page(source, budget=128 * 1024, limit=15)
    [turn] = result["turns"]
    # Deep elision cannot fit 200 KB of prose: the shallow turn is served whole.
    assert snapshot_byte_size(result) > 128 * 1024
    results = [p for p in turn["parts"] if p["type"] == "tool_result"]
    assert all(p["content"] == "r" * 600 for p in results)


def test_deep_elision_never_grows_a_part_and_keeps_questions_whole():
    from skuld.conversation_shallow import elide_part_deep

    tiny = {"type": "tool_result", "tool_use_id": "t", "content": "ok"}
    assert elide_part_deep(tiny) is tiny
    ask = {
        "type": "tool_use",
        "id": "q",
        "name": "AskUserQuestion",
        "input": {"questions": [{"question": "x" * 900, "options": []}]},
    }
    assert elide_part_deep(ask) is ask
    edit = {
        "type": "tool_use",
        "id": "e",
        "name": "Edit",
        "input": {"file_path": "a.py", "old_string": "o" * 400, "new_string": "n" * 400},
    }
    deep = elide_part_deep(edit)
    assert deep["input"]["_elided_input"] is True and deep["input"]["preview"] == "a.py"
    assert snapshot_byte_size(deep) < snapshot_byte_size(edit)
