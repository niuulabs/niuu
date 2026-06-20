"""Small resident domain-drive primitives.

This module is deliberately modest: it turns a short resident mandate into a
first orientation pass that a Valkyrie can use to create its own work. It is
not a scheduler, memory architecture, or UI flow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class OperatorContactKind(StrEnum):
    ASK_USER = "ask_user"
    HELP_NEEDED = "help_needed"


@dataclass(frozen=True)
class DomainHypothesis:
    subject: str
    belief: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.5
    inferred: bool = True


@dataclass(frozen=True)
class DomainQuestion:
    question: str
    reason: str
    impact: str
    blocks_action: bool = False


@dataclass(frozen=True)
class SelfAuthoredWork:
    title: str
    kind: str
    reason: str
    safe_to_start: bool
    next_step: str
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CapabilityGap:
    capability: str
    reason: str
    safe_next_step: str
    gated_actions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SafeNextAction:
    title: str
    reason: str
    action: str
    requires_human: bool = False


@dataclass(frozen=True)
class DomainDriveOrientation:
    mandate: str
    orientation_summary: str
    hypotheses: list[DomainHypothesis]
    open_questions: list[DomainQuestion]
    self_authored_work: list[SelfAuthoredWork]
    capability_gaps: list[CapabilityGap]
    selected_next_action: SafeNextAction

    def to_dict(self) -> dict:
        return {
            "mandate": self.mandate,
            "orientation_summary": self.orientation_summary,
            "domain_hypotheses": [asdict(hypothesis) for hypothesis in self.hypotheses],
            "open_questions": [asdict(question) for question in self.open_questions],
            "self_authored_work": [asdict(work) for work in self.self_authored_work],
            "capability_gaps": [asdict(gap) for gap in self.capability_gaps],
            "selected_next_action": asdict(self.selected_next_action),
        }


@dataclass(frozen=True)
class OperatorContactRequest:
    kind: OperatorContactKind
    question: str
    reason: str
    impact: str
    tool_name: str = ""
    tool_input: dict[str, str] = field(default_factory=dict)
    help_needed_outcome: dict[str, object] = field(default_factory=dict)


def orient_domain_from_mandate(
    mandate: str,
    *,
    available_context: list[str] | tuple[str, ...] = (),
    available_tools: list[str] | tuple[str, ...] = (),
) -> DomainDriveOrientation:
    """Return a first self-authored orientation from a small domain mandate."""
    clean_mandate = " ".join(str(mandate or "").split())
    if not clean_mandate:
        raise ValueError("domain mandate must not be empty")

    lowered = clean_mandate.casefold()
    evidence = _evidence_from_mandate(lowered)
    context_evidence = [f"available context: {item}" for item in available_context if item]
    tool_evidence = [f"available tool: {item}" for item in available_tools if item]

    hypotheses = _generic_hypotheses(evidence)
    questions = _generic_questions()
    work = _generic_self_authored_work(evidence + context_evidence)
    gaps = _generic_capability_gaps(evidence + tool_evidence)
    summary = (
        "This is a resident domain mandate. Start by mapping what exists, "
        "what the operator cares about, what can be safely inspected, and "
        "what decisions need human judgment."
    )

    selected = SafeNextAction(
        title="Orient to the domain and map the first useful work",
        reason=(
            "The mandate gives responsibility but no task; the safest useful "
            "move is to inspect available context and turn inferred concerns "
            "into evidence-backed work."
        ),
        action=(
            "Review available files, notes, tools, and integrations; write a "
            "short domain map with hypotheses, unknowns, and the first safe "
            "improvement candidates."
        ),
        requires_human=False,
    )

    return DomainDriveOrientation(
        mandate=clean_mandate,
        orientation_summary=summary,
        hypotheses=hypotheses,
        open_questions=questions,
        self_authored_work=work,
        capability_gaps=gaps,
        selected_next_action=selected,
    )


def choose_operator_contact(
    orientation: DomainDriveOrientation,
    *,
    interactive: bool,
) -> OperatorContactRequest | None:
    """Return the first high-value operator question as an existing contact path."""
    question = next((item for item in orientation.open_questions if item.blocks_action), None)
    if question is None:
        question = orientation.open_questions[0] if orientation.open_questions else None
    if question is None:
        return None

    if interactive:
        return OperatorContactRequest(
            kind=OperatorContactKind.ASK_USER,
            question=question.question,
            reason=question.reason,
            impact=question.impact,
            tool_name="ask_user",
            tool_input={"question": question.question},
        )

    return OperatorContactRequest(
        kind=OperatorContactKind.HELP_NEEDED,
        question=question.question,
        reason=question.reason,
        impact=question.impact,
        help_needed_outcome={
            "verdict": "help_needed",
            "reason": "needs_context",
            "summary": question.question,
            "attempted": [
                "oriented from the resident mandate",
                "identified inferred domain concerns",
                "selected safe work that does not require this answer",
            ],
            "recommendation": "Reply with the missing domain guidance when convenient.",
            "context": {
                "impact": question.impact,
                "mandate": orientation.mandate,
            },
        },
    )


def _evidence_from_mandate(lowered: str) -> list[str]:
    evidence: list[str] = []
    if "company" in lowered or "business" in lowered:
        evidence.append("operator described a business/company")
    if "3d" in lowered or "3-d" in lowered or "printing" in lowered:
        evidence.append("operator described a 3D printing domain")
    if "resident ravn" in lowered or "resident" in lowered:
        evidence.append("operator assigned resident responsibility")
    if "spending" in lowered or "money" in lowered:
        evidence.append("operator gated spending")
    if "physical" in lowered or "machine" in lowered or "printer" in lowered:
        evidence.append("operator gated physical operation")
    return evidence or ["operator supplied a resident domain mandate"]


def _generic_hypotheses(evidence: list[str]) -> list[DomainHypothesis]:
    return [
        DomainHypothesis(
            subject="operating model",
            belief=(
                "The domain has recurring work, constraints, and improvement "
                "opportunities to discover."
            ),
            reason="The mandate assigns ongoing responsibility rather than a one-off task.",
            evidence=evidence,
            confidence=0.55,
        )
    ]


def _generic_questions() -> list[DomainQuestion]:
    return [
        DomainQuestion(
            question="What existing systems, files, or tools should I inspect first?",
            reason="The resident should verify the domain before inventing work.",
            impact="Determines the first safe discovery path.",
            blocks_action=True,
        )
    ]


def _generic_self_authored_work(evidence: list[str]) -> list[SelfAuthoredWork]:
    return [
        SelfAuthoredWork(
            title="Map what exists in the domain",
            kind="discovery",
            reason="A resident needs a grounded view of the place it is responsible for.",
            safe_to_start=True,
            next_step="Inspect available context and write a concise domain map.",
            evidence=evidence,
        )
    ]


def _generic_capability_gaps(evidence: list[str]) -> list[CapabilityGap]:
    return [
        CapabilityGap(
            capability="domain-specific observation",
            reason="The resident needs a safe way to observe the domain before acting in it.",
            safe_next_step="Discover existing read-only tools and integrations.",
            evidence=evidence,
        )
    ]
