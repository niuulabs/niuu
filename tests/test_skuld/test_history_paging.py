"""Protocol2 paging: stable seams, growth continuations and immutable originals."""

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


def page(turns, cursor=None, *, limit=5, budget=8192, sid="session", revision=None):
    frame = {"turns": turns}
    if revision:
        frame["projection_revision"] = revision
    return prepare_history_page(
        frame, session_id=sid, max_turns=limit, max_bytes=budget, cursor=cursor
    )


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
def test_one_huge_item_is_explicit_preview_with_full_item_reference(huge):
    source = rows(1)
    content = "x" * 150_000 if huge != "control_chars" else "\0" * 150_000
    if huge == "metadata":
        source[0]["metadata"] = {"evidence": content}
        source[0]["visibility"] = "internal"
        source[0]["participant_id"] = "synthetic-internal-sender"
    elif huge == "tools":
        source[0]["parts"] = [
            {"type": "tool_use", "id": f"call-{i}", "name": "Bash", "input": {}}
            for i in range(5000)
        ]
    else:
        source[0]["content"] = content
    original = copy.deepcopy(source)
    result = page(source)
    assert snapshot_byte_size(result) <= 8192
    assert result["history_preview"] is True
    turn = result["turns"][0]
    assert turn["history_ref"] == {"turn_id": "0"}
    assert turn["parts"] == []  # no orphan result falsely presented as complete
    assert source == original
    if huge == "metadata":
        assert turn["history_metadata_preview"] is True
        assert turn["visibility"] == "internal"
        assert turn["participant_id"] == "synthetic-internal-sender"
    assert result["window_offset"] == 0 and result["total_turns"] == 1


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


def test_one_huge_part_array_fits_without_quadratic_full_serializations(monkeypatch):
    from skuld import conversation_snapshot as module

    actual = module.snapshot_byte_size
    calls = []

    def measured(frame):
        calls.append(len(frame["turns"][0].get("parts") or []))
        return actual(frame)

    monkeypatch.setattr(module, "snapshot_byte_size", measured)
    source = rows(1)
    source[0]["parts"] = [{"type": "text", "text": "synthetic" * 20} for _ in range(10_000)]
    result = page(source)
    assert result["history_preview"]
    assert len(calls) < 30


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
