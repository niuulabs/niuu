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


def test_classify_text_covers_every_rule_and_helpers() -> None:
    from ravn.resident_inbox.classify import _contains_url, _keywords

    cls = ResidentInboxClassification
    cases = {
        "this request is denied": cls.DENIAL,
        "approved, proceed": cls.APPROVAL,
        "policy: never deploy on friday": cls.POLICY,
        "i prefer matte filament": cls.PREFERENCE,
        "actually that is wrong, fix that": cls.CORRECTION,
        "can you investigate pricing": cls.TASK_REQUEST,
        "maybe we could add a bundle": cls.IDEA,
        # Domain language has no hardcoded meaning; the resident LLM receives
        # the original text and decides what it represents.
        "the printer filament ran out": cls.FACT,
        "this is a security risk": cls.RISK,
        "status: blocked waiting": cls.STATUS_UPDATE,
        "see notes.md for details": cls.FILE_REFERENCE,
        "the weather is calm today": cls.FACT,
    }
    for text, expected in cases.items():
        assert classify_text(text)[0] == expected.value, text

    # environment-signal payloads are treated as source evidence
    assert classify_text("queue", payload={"kind": "signal.host"})[0] == cls.SOURCE_EVIDENCE.value
    assert _contains_url("visit https://example.com")
    keywords = _keywords("resident pricing strategy")
    assert "pricing" in keywords and "resident" not in keywords
