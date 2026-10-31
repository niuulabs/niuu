"""Fleet feed merge and opaque per-node cursors."""

from __future__ import annotations

import pytest

from niuu.adapters.inbound.forge_notification_feed import (
    CursorError,
    InstancePage,
    decode_cursor,
    encode_cursor,
    merge_after,
    merge_newest,
)


def _item(instance: str, seq: int, minute: int) -> dict:
    return {"instance_id": instance, "seq": seq, "created_at": f"2026-09-23T10:{minute:02d}:00Z"}


def _page(items, *, next_before=None, head=0) -> InstancePage:
    return InstancePage(
        items=items, next_before=next_before, head_seq=head, read_through_seq=0, unread_count=0
    )


def test_cursor_round_trip_is_unpadded_base64url():
    token = encode_cursor({"b": 5, "a": None})
    assert "=" not in token
    assert decode_cursor(token) == {"a": None, "b": 5}


@pytest.mark.parametrize("token", ["!!", encode_cursor({"": 1}), encode_cursor({"a": True})])
def test_invalid_cursors(token):
    with pytest.raises(CursorError):
        decode_cursor(token)


def test_node_rows_stay_a_prefix_even_when_clocks_disagree():
    # Node "a" committed seq 9 before seq 8 by wall clock (clock step): the merge
    # must still take a's rows in seq order, so its cursor never skips seq 8.
    pages = {
        "a": _page([_item("a", 9, 1), _item("a", 8, 7)], next_before=8, head=9),
        "b": _page([_item("b", 3, 5), _item("b", 2, 4)], next_before=2, head=3),
    }
    merged = merge_newest(pages, previous={}, unavailable=set(), limit=2)
    assert [(i["instance_id"], i["seq"]) for i in merged.items] == [("b", 3), ("b", 2)]
    assert merged.next_before == {"a": None, "b": 2}


def test_exhausted_nodes_leave_the_cursor_and_unavailable_ones_keep_it():
    pages = {"a": _page([_item("a", 1, 1)], head=1)}
    merged = merge_newest(pages, previous={"a": 2, "c": 7}, unavailable={"c"}, limit=5)
    assert merged.next_before == {"c": 7}
    assert merged.next_after == {"a": 1}
    done = merge_newest(pages, previous={}, unavailable=set(), limit=None)
    assert done.next_before is None and len(done.items) == 1


def test_gap_fill_keeps_every_row_and_advances_per_node():
    pages = {
        "a": _page([_item("a", 5, 2), _item("a", 4, 1)]),
        "b": _page([]),
    }
    merged = merge_after(pages, previous={"b": 7, "c": 3}, unavailable={"c"})
    assert [i["seq"] for i in merged.items] == [4, 5]
    assert merged.next_after == {"a": 5, "b": 7, "c": 3}
    assert merged.next_before is None


def test_rows_without_a_parseable_timestamp_sort_oldest():
    pages = {
        "a": _page([{"instance_id": "a", "seq": 2, "created_at": None}], head=2),
        "b": _page([{"instance_id": "b", "seq": 1, "created_at": "not a time"}], head=1),
        "c": _page([_item("c", 1, 1)], head=1),
    }
    merged = merge_newest(pages, previous={}, unavailable=set(), limit=None)
    assert merged.items[0]["instance_id"] == "c"
