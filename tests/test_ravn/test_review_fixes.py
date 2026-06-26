"""Regression tests for correctness bugs found in the resident-drive code review."""

from __future__ import annotations

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
