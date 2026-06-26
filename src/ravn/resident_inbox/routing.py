"""Route classified inbox signals into resident objectives and outcomes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
)
from ravn.resident_continuation import _compact_line, _slug
from ravn.resident_text import append_unique as _append_unique

from .classify import _keywords
from .models import (
    _OPERATOR_DIRECTED_MESSAGE_KIND,
    ResidentInboxClassification,
    ResidentInboxConfig,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)


def _objective_for_signal(
    mandate: str,
    signal_ref: str,
    signal: ResidentInboxSignal,
    objectives: tuple[ResidentObjective, ...],
    *,
    config: ResidentInboxConfig,
) -> tuple[ResidentObjective | None, str]:
    if config.attach_to_existing_objectives:
        existing = _best_matching_objective(signal, objectives, config.min_attach_score)
        if existing is not None:
            return (
                existing.with_updates(
                    source_evidence=_append_unique(existing.source_evidence, signal_ref),
                    proof_progress=_append_unique(
                        existing.proof_progress,
                        f"inbox signal attached: {signal.id}",
                    ),
                    last_reviewed_at=datetime.now(UTC),
                ),
                ResidentInboxStatus.ATTACHED.value,
            )
    if not config.create_objectives:
        return None, ResidentInboxStatus.IGNORED.value
    objective = ResidentObjective(
        id=f"inbox-{_slug(signal.summary)[:80] or _slug(signal.id) or 'signal'}",
        title=f"Follow up inbox signal: {_compact_line(signal.summary, limit=80)}",
        purpose="Turn a resident inbox signal into useful, safe domain progress.",
        serves_mandate_because=(
            "The resident received new external or operator-provided context and should "
            "decide whether it changes the work portfolio."
        ),
        expected_outcome="A researched, attached, or resolved resident follow-up with evidence.",
        proof_criteria=(
            "The inbox signal is cited as source evidence.",
            "The resident records whether it acted, ignored, asked, or delegated.",
            "No spend, physical operation, external account change, or customer contact "
            "occurs without approval.",
        ),
        kind=_objective_kind_for_signal(signal),
        risk_boundaries=_risk_boundaries_for_signal(signal),
        priority_score=_priority_for_signal(signal),
        priority_band="inbox",
        priority_rationale=f"Inbox classification {signal.classification}: {signal.reason}",
        source_evidence=(signal_ref,),
        reasoning=(
            f"Created from resident inbox signal {signal.id}. "
            f"Mandate: {_compact_line(mandate, limit=160)}"
        ),
    )
    return objective, ResidentInboxStatus.CONVERTED.value


def _operator_resolution_for_signal(
    signal: ResidentInboxSignal,
    objectives: tuple[ResidentObjective, ...],
) -> ResidentObjective | None:
    # Only an operator-directed message may resolve a pending objective. Environment
    # signals that incidentally contain "yes"/"no" must never clear an operator gate.
    if signal.kind != _OPERATOR_DIRECTED_MESSAGE_KIND:
        return None
    approval = _approval_for_signal(signal)
    if approval is None:
        return None
    pending = tuple(
        objective
        for objective in objectives
        if objective.pending_question
        or objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
    )
    if len(pending) != 1:
        return None
    objective = pending[0]
    marker = (
        "operator inbox approval"
        if approval
        else "operator inbox denial"
    )
    return objective.with_updates(
        status=(
            ResidentObjectiveStatus.CANDIDATE.value
            if approval
            else ResidentObjectiveStatus.BLOCKED.value
        ),
        pending_question="",
        source_evidence=_append_unique(objective.source_evidence, signal.id),
        proof_progress=_append_unique(
            objective.proof_progress,
            f"{marker}: {signal.summary}",
        ),
        last_reviewed_at=datetime.now(UTC),
    )


def _approval_for_signal(signal: ResidentInboxSignal) -> bool | None:
    if signal.classification == ResidentInboxClassification.DENIAL.value:
        return False
    if signal.classification != ResidentInboxClassification.APPROVAL.value:
        return None
    text = f"{signal.summary} {json.dumps(signal.payload, default=str)}".casefold()
    if any(item in text for item in ("not approved", "do not", "don't", "deny", "denied")):
        return False
    if any(item in text for item in ("approved", "yes", "use ", "go ahead", "answered")):
        return True
    return None


def _best_matching_objective(
    signal: ResidentInboxSignal,
    objectives: tuple[ResidentObjective, ...],
    min_score: int,
) -> ResidentObjective | None:
    signal_terms = set(_keywords(signal.summary))
    best: tuple[int, ResidentObjective] | None = None
    for objective in objectives:
        objective_terms = set(
            _keywords(
                " ".join(
                    (
                        objective.id,
                        objective.title,
                        objective.purpose,
                        " ".join(objective.source_evidence),
                    )
                )
            )
        )
        score = len(signal_terms & objective_terms)
        if score >= min_score and (best is None or score > best[0]):
            best = (score, objective)
    return best[1] if best is not None else None


def _objective_kind_for_signal(signal: ResidentInboxSignal) -> str:
    if signal.classification == ResidentInboxClassification.PHYSICAL_OBSERVATION.value:
        return ResidentObjectiveKind.VERIFICATION.value
    if signal.classification == ResidentInboxClassification.IDEA.value:
        return ResidentObjectiveKind.CREATIVE_EXPLORATION.value
    return ResidentObjectiveKind.RESEARCH.value


def _risk_boundaries_for_signal(signal: ResidentInboxSignal) -> tuple[str, ...]:
    text = f"{signal.summary} {json.dumps(signal.payload, default=str)}".casefold()
    risks: list[str] = []
    if any(item in text for item in ("spend", "buy", "purchase", "ads", "subscription")):
        risks.append("spending")
    if any(item in text for item in ("printer", "print failed", "physical", "machine")):
        risks.append("physical_operation")
    if any(item in text for item in ("customer", "private", "email", "address")):
        risks.append("customer_or_private_data")
    return tuple(risks)


def _priority_for_signal(signal: ResidentInboxSignal) -> int:
    base = int(max(1.0, min(10.0, signal.confidence * 10)))
    if signal.classification in {
        ResidentInboxClassification.TASK_REQUEST.value,
        ResidentInboxClassification.SOURCE_EVIDENCE.value,
    }:
        base += 8
    if signal.classification == ResidentInboxClassification.PHYSICAL_OBSERVATION.value:
        base += 6
    return min(base, 25)




def _inbox_outcomes(signal: ResidentInboxSignal) -> tuple[str, ...]:
    outcomes = ["resident awareness", "lower manual effort"]
    if signal.kind.startswith("signal."):
        outcomes = (*outcomes, "environment health", "reliable operation")
    if signal.classification in {
        ResidentInboxClassification.SOURCE_EVIDENCE.value,
        ResidentInboxClassification.URL_REFERENCE.value,
    }:
        outcomes = (*outcomes, "better source-backed decisions")
    if signal.classification == ResidentInboxClassification.PHYSICAL_OBSERVATION.value:
        outcomes = (*outcomes, "printability and quality")
    return outcomes


def _inbox_next_action(triages: list[ResidentInboxTriage]) -> str:
    if not triages:
        return "Sleep until a new resident inbox signal arrives."
    converted = [item for item in triages if item.decision == ResidentInboxStatus.CONVERTED.value]
    attached = [item for item in triages if item.decision == ResidentInboxStatus.ATTACHED.value]
    remembered = [item for item in triages if item.decision == ResidentInboxStatus.REMEMBERED.value]
    if converted:
        return f"Review and advance {len(converted)} inbox-created objective(s)."
    if attached:
        return f"Advance objective(s) updated with {len(attached)} inbox signal(s)."
    if remembered:
        return f"Use {len(remembered)} remembered inbox observation(s) in future decisions."
    return "No actionable inbox signal remained after triage."
