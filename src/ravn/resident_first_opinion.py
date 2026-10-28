"""A System One first opinion on every resident turn, compared with the turn's own judgment.

Every resident turn ends in an outcome block whose ``tier`` and ``decision`` the ODIN court
resolves. Producing them costs a full model turn, even when the answer is "nothing warrants
action". A decision model can give the same two judgments in one cheap request — the question is
whether it agrees with the resident often enough, and on the right cases, to be trusted with
any of them.

This module answers that question and nothing else. Before the turn it asks the decision model
for its tier and decision on the same observation the resident is about to judge; after the turn
it records both side by side. **The first opinion changes nothing**: the turn always runs, and
its outcome is the only one that reaches the court. What it produces is evidence:

* ``ravn.decision.first_opinion.comparisons`` counted by question, first opinion, resident answer
  and agreement — the agreement matrix per tier and per decision;
* ``ravn.decision.first_opinion.tier_delta`` — first-opinion tier index minus the resident's,
  so a negative value is the dangerous direction (the cheap model would have under-attended);
* a ``ravn.decision.first_opinion`` span event with both full judgments;
* one INFO log line per comparison carrying the trace id, so disagreements can be listed and
  opened from the logs.

The tier and decision descriptions below describe the contract's vocabulary — what each value
causes downstream — not what any signal means. Their keys are tested against the vocabulary
tuples so the two cannot drift apart.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from niuu.observability import get_observability
from ravn.cap_telemetry import record_truncation
from ravn.domain.models import AgentTask
from ravn.domain.valkyrie_contracts import (
    VALKYRIE_ATTENTION_TIER_ORDER,
    VALKYRIE_DECISION_ORDER,
)
from ravn.ports.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionPort,
    DecisionQuestion,
    NoulAnswer,
    NoulQuestion,
)

logger = logging.getLogger(__name__)

PURPOSE = "resident.first_opinion"
_UNAVAILABLE = "unavailable"
#: Heading every resident task context uses for its outcome contract
#: (``environment_signal_runtime`` and ``valkyrie_contracts.resident_outcome_section``).
_OUTCOME_HEADING = "## Required outcome"

#: What each attention tier causes when the ODIN court resolves it (``court._decision_for``).
TIER_CRITERIA: dict[str, str] = {
    "silent": "Nothing anyone needs to see; it needs no record beyond the raw archive.",
    "ambient": "Worth recording in the resident's memory, but it need not reach the operator.",
    "present": "The operator should be notified on their usual surface.",
    "urgent": "Needs the operator now; justifies pulling them into a live huddle.",
}

#: What each resident decision means for the resident's own next step.
DECISION_CRITERIA: dict[str, str] = {
    "ignore": "Routine or outside the mandate; no follow-up at all.",
    "watch": "Not actionable yet; keep an eye on whether it recurs or develops.",
    "investigate": "Cannot be judged yet; more information must be gathered first.",
    "propose_action": "Calls for a concrete change to the environment the resident should propose.",
    "escalate": "The resident cannot decide this alone and needs to ask the operator.",
    "learn": "Teaches something reusable about the environment, worth recording as a learning.",
    "blocked": "The resident cannot act because something it needs is missing or failing.",
}

_QUESTIONS: dict[str, DecisionQuestion] = {
    "tier": ChoiceQuestion(
        instructions=(
            "A resident steward with the duty described in `mandate` has received `observation`. "
            "How much of the operator's attention does this observation deserve?"
        ),
        options={tier: TIER_CRITERIA[tier] for tier in VALKYRIE_ATTENTION_TIER_ORDER},
    ),
    "decision": ChoiceQuestion(
        instructions=(
            "A resident steward with the duty described in `mandate` has received `observation`. "
            "What should the steward do about it next?"
        ),
        options={decision: DECISION_CRITERIA[decision] for decision in VALKYRIE_DECISION_ORDER},
    ),
    "needs_investigation": NoulQuestion(
        instructions=(
            "Judging `observation` correctly requires the steward to gather more information "
            "(inspect, query or read something) before it can decide."
        ),
    ),
}


@dataclass(frozen=True)
class FirstOpinion:
    """The decision model's judgment of one resident turn, before the turn ran."""

    case_id: str
    model: str
    tier: ChoiceAnswer
    decision: ChoiceAnswer
    needs_investigation: NoulAnswer


class ResidentFirstOpinion:
    """Ask for a first opinion before a resident turn and compare it with the turn's outcome."""

    def __init__(self, decisions: DecisionPort, *, max_observation_chars: int) -> None:
        if max_observation_chars <= 0:
            raise ValueError("max_observation_chars must be positive")
        self._decisions = decisions
        self._max_observation_chars = max_observation_chars

    @staticmethod
    def applies_to(task: AgentTask) -> bool:
        """Only resident cases produce the tier/decision outcome being compared."""
        return bool(task.resident_case_id)

    async def judge(self, task: AgentTask) -> FirstOpinion:
        result = await self._decisions.decide(
            self._state(task),
            _QUESTIONS,
            purpose=PURPOSE,
        )
        tier = result.answers["tier"]
        decision = result.answers["decision"]
        needs_investigation = result.answers["needs_investigation"]
        if not (
            isinstance(tier, ChoiceAnswer)
            and isinstance(decision, ChoiceAnswer)
            and isinstance(needs_investigation, NoulAnswer)
        ):
            raise TypeError("decision port answered resident first-opinion questions mistyped")
        return FirstOpinion(
            case_id=task.resident_case_id,
            model=result.model,
            tier=tier,
            decision=decision,
            needs_investigation=needs_investigation,
        )

    def compare(
        self,
        opinion: FirstOpinion,
        outcome: Mapping[str, Any] | None,
        *,
        outcome_valid: bool,
    ) -> None:
        """Record the first opinion beside the resident's own outcome for this turn."""
        telemetry = get_observability()
        resident_tier = _vocabulary_value(outcome, "tier", VALKYRIE_ATTENTION_TIER_ORDER)
        resident_decision = _vocabulary_value(outcome, "decision", VALKYRIE_DECISION_ORDER)
        base = {
            "ravn.decision.model": opinion.model,
            "ravn.valkyrie.outcome_valid": outcome_valid,
        }
        for question, first, resident in (
            ("tier", opinion.tier.choice, resident_tier),
            ("decision", opinion.decision.choice, resident_decision),
        ):
            attributes: dict[str, Any] = {
                **base,
                "ravn.decision.question": question,
                "ravn.decision.first_opinion": first,
                "ravn.decision.resident": resident,
            }
            if resident != _UNAVAILABLE:
                attributes["ravn.decision.agreed"] = first == resident
            telemetry.count("ravn.decision.first_opinion.comparisons", attributes=attributes)
        logger.info(
            "first_opinion[%s] model=%s outcome_valid=%s tier jev=%s resident=%s agreed=%s "
            "decision jev=%s resident=%s agreed=%s",
            opinion.case_id,
            opinion.model,
            outcome_valid,
            opinion.tier.choice,
            resident_tier,
            _agreement(opinion.tier.choice, resident_tier),
            opinion.decision.choice,
            resident_decision,
            _agreement(opinion.decision.choice, resident_decision),
        )
        if resident_tier != _UNAVAILABLE:
            delta = VALKYRIE_ATTENTION_TIER_ORDER.index(
                opinion.tier.choice
            ) - VALKYRIE_ATTENTION_TIER_ORDER.index(resident_tier)
            telemetry.record(
                "ravn.decision.first_opinion.tier_delta",
                delta,
                attributes={**base, "ravn.decision.resident": resident_tier},
            )
        telemetry.event(
            "ravn.decision.first_opinion",
            attributes={
                **base,
                "ravn.resident.case_id": opinion.case_id,
                "ravn.decision.first_opinion.tier": opinion.tier.choice,
                "ravn.decision.first_opinion.decision": opinion.decision.choice,
                "ravn.decision.resident.tier": resident_tier,
                "ravn.decision.resident.decision": resident_decision,
            },
            content={
                "first_opinion": {
                    "tier": opinion.tier.choice,
                    "tier_confidence": opinion.tier.confidence,
                    "tier_probabilities": dict(opinion.tier.probabilities),
                    "decision": opinion.decision.choice,
                    "decision_confidence": opinion.decision.confidence,
                    "decision_probabilities": dict(opinion.decision.probabilities),
                    "needs_investigation": opinion.needs_investigation.probability,
                },
                "resident": {
                    "tier": resident_tier,
                    "decision": resident_decision,
                    "confidence": (outcome or {}).get("confidence"),
                },
            },
        )

    def _state(self, task: AgentTask) -> dict[str, str]:
        observation = without_outcome_contract(task.initiative_context)
        if len(observation) > self._max_observation_chars:
            record_truncation(
                "decision.resident_first_opinion.max_observation_chars",
                what=f"resident case {task.resident_case_id} observation for the first opinion",
                kept=self._max_observation_chars,
                total=len(observation),
            )
            observation = observation[: self._max_observation_chars]
        return {
            "mandate": task.resident_mandate,
            "trigger": task.triggered_by,
            "title": task.title,
            "observation": observation,
        }


def without_outcome_contract(context: str) -> str:
    """Drop the ``## Required outcome`` section a resident task context ends with.

    It is formatting instructions for the turn — a YAML template listing every tier and decision
    value — and as decision-model state it is pure distractor that also names the answer options.
    The section runs to the next ``## `` heading or the end of the context.
    """
    start = context.find(_OUTCOME_HEADING)
    if start < 0:
        return context
    end = context.find("\n## ", start + len(_OUTCOME_HEADING))
    rest = context[end + 1 :] if end >= 0 else ""
    return (context[:start] + rest).rstrip()


def _agreement(first: str, resident: str) -> str:
    if resident == _UNAVAILABLE:
        return _UNAVAILABLE
    return str(first == resident).lower()


def _vocabulary_value(
    outcome: Mapping[str, Any] | None,
    field: str,
    vocabulary: tuple[str, ...],
) -> str:
    """The outcome's value for *field* when it is in the contract vocabulary."""
    if outcome is None:
        return _UNAVAILABLE
    value = outcome.get(field)
    if isinstance(value, str) and value.strip() in vocabulary:
        return value.strip()
    return _UNAVAILABLE
