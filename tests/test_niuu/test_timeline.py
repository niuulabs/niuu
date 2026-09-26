"""Unit tests for niuu.domain.timeline — shared Timeline-zone date parsing."""

from __future__ import annotations

from datetime import UTC, datetime

from niuu.domain.timeline import extract_entry_dates, extract_zone, parse_dated_entry


def test_extract_zone_returns_body_up_to_next_heading():
    body = "## Timeline\n\n- line one\n- line two\n\n## Compiled Truth\n\nOther stuff\n"
    assert extract_zone(body, "## Timeline") == "- line one\n- line two"


def test_extract_zone_returns_empty_when_heading_absent():
    assert extract_zone("no headings here", "## Timeline") == ""


def test_extract_zone_runs_to_end_of_document_when_last():
    body = "## Timeline\n\n- only entry\n"
    assert extract_zone(body, "## Timeline") == "- only entry"


def test_parse_dated_entry_extracts_date_and_rest():
    result = parse_dated_entry("- 2026-01-02: Something happened. [Source: test]")
    assert result == ("2026-01-02", "Something happened. [Source: test]")


def test_parse_dated_entry_returns_none_for_non_list_line():
    assert parse_dated_entry("Something happened. [Source: test]") is None


def test_parse_dated_entry_returns_none_without_date_prefix():
    assert parse_dated_entry("- Undated entry [Source: test]") is None


def test_parse_dated_entry_handles_surrounding_whitespace():
    assert parse_dated_entry("   - 2026-01-02: text  ") == ("2026-01-02", "text")


def test_extract_entry_dates_returns_utc_midnight_datetimes():
    content = "## Timeline\n\n- 2020-01-01: First. [Source: a]\n- 2021-06-15: Second. [Source: b]\n"
    assert extract_entry_dates(content) == [
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2021, 6, 15, tzinfo=UTC),
    ]


def test_extract_entry_dates_skips_undated_entries():
    content = "## Timeline\n\n- Undated. [Source: a]\n- 2020-01-01: Dated. [Source: b]\n"
    assert extract_entry_dates(content) == [datetime(2020, 1, 1, tzinfo=UTC)]


def test_extract_entry_dates_empty_without_timeline_zone():
    assert extract_entry_dates("# Page\n\nNo timeline here.\n") == []


def test_extract_entry_dates_ignores_frontmatter():
    content = "---\ntitle: Test\n---\n## Timeline\n\n- 2020-01-01: Entry. [Source: a]\n"
    assert extract_entry_dates(content) == [datetime(2020, 1, 1, tzinfo=UTC)]


def test_extract_entry_dates_skips_impossible_calendar_dates():
    content = "## Timeline\n\n- 2026-02-30: Typo. [Source: x]\n- 2026-01-02: Real. [Source: y]\n"
    assert [d.date().isoformat() for d in extract_entry_dates(content)] == ["2026-01-02"]
