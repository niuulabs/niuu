"""Tests locking the behavior of the shared resident text helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from ravn.resident_text import (
    append_unique,
    compact_line,
    merge_unique,
    metadata,
    render_list,
    section,
    section_items,
    section_lines,
    slug,
    timestamp_slug,
)


def test_slug_collapses_and_truncates() -> None:
    assert slug("Hello, World!") == "hello-world"
    assert slug("a__b  c") == "a-b-c"
    assert slug("résumé café") == "r-sum-caf"
    assert slug("  --weird-- ") == "weird"


def test_slug_max_length_and_fallback() -> None:
    assert slug("x" * 200) == "x" * 80
    assert slug("y" * 200, max_length=96) == "y" * 96
    assert slug("", fallback="") == ""
    assert slug("@@@", fallback="item") == "item"
    assert slug("ok", fallback="item") == "ok"


def test_compact_line_whitespace_and_marker() -> None:
    assert compact_line("  many   spaces\nhere ") == "many spaces here"
    assert compact_line("abcdef", limit=4) == "abc…"
    assert compact_line("abcdef", limit=4, marker="...") == "abc..."
    assert compact_line("short", limit=240) == "short"


def test_timestamp_slug_format() -> None:
    stamp = timestamp_slug(datetime(2026, 6, 26, 8, 9, 10, 123456, tzinfo=UTC))
    assert stamp == "20260626T080910123456Z"


def test_merge_unique_preserves_order_and_dedupes() -> None:
    assert merge_unique(("a", "b"), ("b", "c")) == ("a", "b", "c")
    assert merge_unique((), ()) == ()


def test_append_unique() -> None:
    assert append_unique(("a",), "b") == ("a", "b")
    assert append_unique(("a",), "a") == ("a",)
    assert append_unique(("a",), "  ") == ("a",)
    assert append_unique(("a",), " b ") == ("a", "b")


_CHRONICLE = """# Title

- id: thing-1
- status: active

## Summary

Some prose line.
More prose.

## Items

- first
- none
- second

## Empty
"""


def test_metadata_parses_dash_key_lines() -> None:
    assert metadata(_CHRONICLE) == {"id": "thing-1", "status": "active"}


def test_section_returns_prose_only() -> None:
    assert section(_CHRONICLE, "Summary") == "Some prose line.\nMore prose."
    assert section(_CHRONICLE, "Missing") == ""


def test_section_items_skips_none_sentinel() -> None:
    assert section_items(_CHRONICLE, "Items") == ["first", "second"]
    assert section_items(_CHRONICLE, "Empty") == []


def test_section_lines_stops_at_next_heading() -> None:
    assert section_lines(_CHRONICLE, "Items") == ["- first", "- none", "- second"]


def test_render_list_handles_empty() -> None:
    assert render_list(["a", " ", "b"]) == "- a\n- b"
    assert render_list([]) == "- none"
