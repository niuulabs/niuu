"""Regression tests for correctness bugs found in the resident-drive code review."""

from __future__ import annotations

from datetime import UTC, datetime

from ravn.resident_inbox.classify import classify_text
from ravn.resident_inbox.models import ResidentInboxClassification

# --- approval-with-"no" must not be read as denial ----------------------------


def test_classify_text_no_problem_is_not_denial() -> None:
    classification, _, _ = classify_text("no problem, go ahead")
    assert classification != ResidentInboxClassification.DENIAL.value
    assert classify_text("no")[0] == ResidentInboxClassification.DENIAL.value


# --- robustness / silent-wrong fixes ------------------------------------------


def test_inbox_parse_datetime_tolerates_malformed_dates() -> None:
    from ravn.resident_inbox.serialization import _parse_datetime

    # a malformed/legacy date must not raise (would crash the whole wake pass)
    assert _parse_datetime("2026/06/26") is not None
    assert _parse_datetime("not-a-date") is not None


def test_consumed_marker_always_records_status() -> None:
    from ravn.resident_continuation import (
        _operator_answer_is_consumed,
        _render_consumed_operator_answer,
    )

    # non-canonical answer content (no status line, no canonical header)
    rendered = _render_consumed_operator_answer("custom note", consumed_at=datetime.now(UTC))
    assert _operator_answer_is_consumed(rendered)
