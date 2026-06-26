"""Regression tests for correctness bugs found in the resident-drive code review."""

from __future__ import annotations

from datetime import UTC, datetime

from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveStatus,
)
from ravn.resident_inbox.classify import classify_text
from ravn.resident_inbox.models import (
    _OPERATOR_DIRECTED_MESSAGE_KIND,
    ResidentInboxClassification,
    ResidentInboxSignal,
)
from ravn.resident_inbox.routing import _operator_resolution_for_signal
from ravn.resident_operator_contact import _operator_reply_approval
from ravn.resident_portfolio.helpers import (
    _merge_text,
    _parse_objective,
    _render_objective,
    select_objectives,
)


def _objective(**overrides) -> ResidentObjective:
    base = {
        "id": "obj-1",
        "title": "Bounded objective",
        "purpose": "advance bounded work",
        "serves_mandate_because": "it advances the mandate",
        "expected_outcome": "evidence exists",
        "proof_criteria": ("evidence exists",),
    }
    base.update(overrides)
    return ResidentObjective(**base)


def _signal(**overrides) -> ResidentInboxSignal:
    base = {"id": "sig-1", "source": "skuld:directed_message", "kind": "signal", "summary": ""}
    base.update(overrides)
    return ResidentInboxSignal(**base)


# --- approval-with-"no" must not be read as denial ----------------------------


def test_operator_reply_approval_allows_no_prefixed_approvals() -> None:
    assert _operator_reply_approval("No problem, go ahead") is True
    assert _operator_reply_approval("Approved, no objections") is True
    assert _operator_reply_approval("yes") is True


def test_operator_reply_approval_still_denies_real_no() -> None:
    assert _operator_reply_approval("no") is False
    assert _operator_reply_approval("no way") is False
    assert _operator_reply_approval("deny") is False


def test_classify_text_no_problem_is_not_denial() -> None:
    classification, _, _ = classify_text("no problem, go ahead")
    assert classification != ResidentInboxClassification.DENIAL.value
    assert classify_text("no")[0] == ResidentInboxClassification.DENIAL.value


# --- only operator-directed messages may resolve a pending objective ----------


def test_operator_resolution_ignores_non_operator_signals() -> None:
    pending = _objective(
        status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
        pending_question="May I proceed?",
    )
    # An environment signal that merely classifies as approval must NOT resolve it.
    env_signal = _signal(
        kind="signal.environment",
        summary="yes the printer is online",
        classification=ResidentInboxClassification.APPROVAL.value,
    )
    assert _operator_resolution_for_signal(env_signal, (pending,)) is None


def test_operator_resolution_applies_to_operator_messages() -> None:
    pending = _objective(
        status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
        pending_question="May I proceed?",
    )
    operator_signal = _signal(
        kind=_OPERATOR_DIRECTED_MESSAGE_KIND,
        summary="yes go ahead",
        classification=ResidentInboxClassification.APPROVAL.value,
    )
    resolved = _operator_resolution_for_signal(operator_signal, (pending,))
    assert resolved is not None
    assert resolved.status == ResidentObjectiveStatus.CANDIDATE.value

    denial_signal = _signal(
        kind=_OPERATOR_DIRECTED_MESSAGE_KIND,
        summary="no, do not proceed",
        classification=ResidentInboxClassification.DENIAL.value,
    )
    blocked = _operator_resolution_for_signal(denial_signal, (pending,))
    assert blocked is not None
    assert blocked.status == ResidentObjectiveStatus.BLOCKED.value


# --- portfolio state correctness ----------------------------------------------


def test_merge_text_keep_last_keeps_newest() -> None:
    existing = tuple(f"entry-{i}" for i in range(40))
    merged = _merge_text(existing, ("entry-new",), limit=40, keep_last=True)
    assert merged[-1] == "entry-new"
    assert len(merged) == 40
    assert "entry-0" not in merged  # oldest dropped, newest retained
    # default (keep-first) is unchanged for other callers
    first = _merge_text(existing, ("entry-new",), limit=40)
    assert "entry-new" not in first


def test_select_objectives_respects_active_capacity() -> None:
    active = [
        _objective(id=f"a{i}", status=ResidentObjectiveStatus.ACTIVE.value) for i in range(3)
    ]
    candidate = _objective(id="cand", status=ResidentObjectiveStatus.CANDIDATE.value)
    # 3 active, max_active=3 -> no capacity, must select nothing
    assert select_objectives((*active, candidate), max_selected=2, max_active=3) == ()
    # one slot free -> selects at most that one slot (not max_selected)
    selected = select_objectives((*active[:2], candidate), max_selected=2, max_active=3)
    assert len(selected) == 1


def test_parse_objective_restores_timestamps() -> None:
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    advanced = datetime(2026, 1, 3, 4, 5, 6, tzinfo=UTC)
    objective = _objective(
        created_at=created,
        updated_at=created,
        last_advanced_at=advanced,
    )
    parsed = _parse_objective(_render_objective(objective))
    assert parsed is not None
    assert parsed.created_at == created
    assert parsed.last_advanced_at == advanced
    assert parsed.last_reviewed_at is None


# --- robustness / silent-wrong fixes ------------------------------------------


def test_inbox_parse_datetime_tolerates_malformed_dates() -> None:
    from ravn.resident_inbox.serialization import _parse_datetime

    # a malformed/legacy date must not raise (would crash the whole wake pass)
    assert _parse_datetime("2026/06/26") is not None
    assert _parse_datetime("not-a-date") is not None


def test_subprocess_payload_tolerates_non_object_json() -> None:
    from ravn.resident_portfolio.helpers import _subprocess_payload

    summary, findings, follow_ups = _subprocess_payload(b'["done"]', b"")
    assert summary  # no AttributeError on list/str JSON
    assert findings == ()
    assert follow_ups == ()


def test_consumed_marker_always_records_status() -> None:
    from ravn.resident_continuation import (
        _operator_answer_is_consumed,
        _render_consumed_operator_answer,
    )

    # non-canonical answer content (no status line, no canonical header)
    rendered = _render_consumed_operator_answer("custom note", consumed_at=datetime.now(UTC))
    assert _operator_answer_is_consumed(rendered)
