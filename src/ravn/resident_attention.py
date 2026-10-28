"""Per-slot System One opinions on resident inbox slots, and the attention gate built on them.

``docs/design/system-one-decision-models.md`` §3.1: a decision model judges each coalescing inbox
slot at the moment a home turn selects it (the evaluation point of
``docs/ravn/ravn-resident-attention.md`` §5.2). Its state is the slot itself — count, span,
ranges, distinct values, newest payload — plus the resident's charter and its last working
state, never the 50-slot turn prompt that the model's known weaknesses warn against.

Two uses, one judgment:

* **Observe** (``decision.resident_first_opinion.enabled``): every selected slot is judged, the
  opinions travel on the home task, and after the turn each is recorded beside the turn's own
  tier and decision. Nothing changes; this is the calibration data.
* **Gate** (``decision.resident_first_opinion.gate.enabled``): an eligible slot whose
  probability of being routine reaches the calibrated threshold gets no turn of its own. It stays
  pending and appears as a receipt — its count and ranges — in the next turn that does run, which
  acknowledges it. Nothing is acknowledged without a turn having seen it.

Whatever is uncertain wakes the resident: critical observations, operator messages, A2A updates
and numerically novel slots are never eligible, and the options are ordered wake-first so the
model's first-option bias leans toward attention, not away from it. A decision backend that fails
raises, exactly like the resident's own model failing.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from niuu.observability import get_observability
from ravn.cap_telemetry import record_truncation
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
from ravn.resident_first_opinion import DECISION_CRITERIA, TIER_CRITERIA
from ravn.resident_inbox import ResidentInboxSignal, aggregate_summary_lines

PURPOSE = "resident.slot_opinion"
#: Severity an operator configured to jump the queue (``critical_reasons``); never gated.
_CRITICAL_SEVERITY = "critical"
#: Inbox kinds and sources whose slots carry authority or a case continuation; never gated.
_OPERATOR_MESSAGE_KIND = "operator.directed_message"
_A2A_SOURCE = "a2a:push"
#: Suffix of a slot that broke out of its shape on a numeric excursion.
_NOVEL_SUFFIX = "-novel"

#: Wake-first option orders. Choice answers lean toward the first option, so the options that
#: send the slot to a full turn come first and the ones that would let it skip come last.
_TIER_OPTIONS = tuple(reversed(VALKYRIE_ATTENTION_TIER_ORDER))
_DECISION_OPTIONS = (
    "escalate",
    "propose_action",
    "investigate",
    "blocked",
    "learn",
    "watch",
    "ignore",
)

QUESTIONS: dict[str, DecisionQuestion] = {
    "tier": ChoiceQuestion(
        instructions=(
            "A resident steward with the duty described in `mandate`, whose current beliefs are "
            "`working_state`, has not yet looked at the observations summarised in `observation`. "
            "How much of the operator's attention do they deserve?"
        ),
        options={tier: TIER_CRITERIA[tier] for tier in _TIER_OPTIONS},
    ),
    "decision": ChoiceQuestion(
        instructions=(
            "A resident steward with the duty described in `mandate`, whose current beliefs are "
            "`working_state`, has not yet looked at the observations summarised in `observation`. "
            "What should the steward do about them next?"
        ),
        options={decision: DECISION_CRITERIA[decision] for decision in _DECISION_OPTIONS},
    ),
    "needs_investigation": NoulQuestion(
        instructions=(
            "Judging the observations in `observation` correctly requires the steward to gather "
            "more information (inspect, query or read something) before it can decide."
        ),
    ),
}


@dataclass(frozen=True)
class SlotOpinion:
    """The decision model's judgment of one inbox slot, taken when the slot was selected."""

    ref: str
    last_archive_ref: str
    model: str
    tier: str
    tier_probabilities: Mapping[str, float]
    decision: str
    decision_probabilities: Mapping[str, float]
    needs_investigation: float
    eligible: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "last_archive_ref": self.last_archive_ref,
            "model": self.model,
            "tier": self.tier,
            "tier_probabilities": dict(self.tier_probabilities),
            "decision": self.decision,
            "decision_probabilities": dict(self.decision_probabilities),
            "needs_investigation": self.needs_investigation,
            "eligible": self.eligible,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> SlotOpinion:
        return cls(
            ref=str(record["ref"]),
            last_archive_ref=str(record.get("last_archive_ref") or ""),
            model=str(record["model"]),
            tier=str(record["tier"]),
            tier_probabilities={
                str(key): float(value) for key, value in record["tier_probabilities"].items()
            },
            decision=str(record["decision"]),
            decision_probabilities={
                str(key): float(value) for key, value in record["decision_probabilities"].items()
            },
            needs_investigation=float(record["needs_investigation"]),
            eligible=bool(record["eligible"]),
        )


@dataclass(frozen=True)
class AttentionGatePolicy:
    """When a slot is routine enough to skip a turn of its own.

    ``skip_probability`` is the model's probability mass on the routine side of every question at
    once — routine tier, routine decision, no investigation needed — so one threshold, fitted on
    ``ravn resident attention-calibrate`` output, decides. Without a threshold nothing is skipped.
    """

    skip_tiers: frozenset[str]
    skip_decisions: frozenset[str]
    min_skip_probability: float | None = None

    def skip_probability(self, opinion: SlotOpinion) -> float:
        tier = sum(opinion.tier_probabilities.get(tier, 0.0) for tier in self.skip_tiers)
        decision = sum(
            opinion.decision_probabilities.get(decision, 0.0) for decision in self.skip_decisions
        )
        return min(tier, decision, 1.0 - opinion.needs_investigation)

    def would_skip(self, opinion: SlotOpinion) -> bool | None:
        """Whether the gate would skip this slot, or None while no threshold is fitted."""
        if self.min_skip_probability is None:
            return None
        return opinion.eligible and self.skip_probability(opinion) >= self.min_skip_probability

    def turn_needed(self, outcome: Mapping[str, Any] | None, *, outcome_valid: bool) -> bool | None:
        """The label: did the resident's own turn judge these observations worth a turn?

        A turn covers several slots but reports one tier and one decision, so the label is
        conservative: a routine conclusion means every slot in the turn could have been skipped,
        anything else means every slot needed it. An invalid outcome is no label at all.
        """
        if not outcome_valid or outcome is None:
            return None
        tier = str(outcome.get("tier") or "").strip()
        decision = str(outcome.get("decision") or "").strip()
        if tier not in VALKYRIE_ATTENTION_TIER_ORDER or decision not in VALKYRIE_DECISION_ORDER:
            return None
        return tier not in self.skip_tiers or decision not in self.skip_decisions


def gate_eligible(signal: ResidentInboxSignal) -> bool:
    """Whether a slot may ever be skipped. Everything that is not is structurally ineligible."""
    if signal.kind == _OPERATOR_MESSAGE_KIND or signal.source == _A2A_SOURCE:
        return False
    if signal.shape_key.endswith(_NOVEL_SUFFIX):
        return False
    severities = set(signal.aggregate.categorical.get("severity", ()))
    severities.add(str(signal.payload.get("severity") or ""))
    return _CRITICAL_SEVERITY not in severities and "severity" not in (
        signal.aggregate.high_cardinality
    )


def slot_observation(ref: str, signal: ResidentInboxSignal) -> str:
    """The slot as decision state: counts and spans computed here, never left to the model."""
    lines = [
        f"inbox_ref: {ref}",
        f"source: {signal.source}",
        f"kind: {signal.kind}",
        f"summary: {signal.summary}",
        f"observations: {signal.observation_count}",
        f"first_observed_at: {signal.first_observed_at or signal.observed_at}",
        f"last_observed_at: {signal.observed_at}",
    ]
    span = _span_seconds(signal.first_observed_at, signal.observed_at)
    if span is not None:
        lines.append(f"observed_span_seconds: {span}")
    if signal.shape_key.endswith(_NOVEL_SUFFIX):
        lines.append("numerically_novel: true (outside the range its shape had established)")
    aggregate = aggregate_summary_lines(signal.aggregate)
    if aggregate:
        lines.append("variation across these observations:")
        lines.extend(line.strip() for line in aggregate)
    raw = signal.payload.get("payload", signal.payload)
    lines.append("newest observation payload:")
    lines.append(json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return "\n".join(lines)


def opinion_mandate(charter: str, personality: str) -> str:
    """The resident's duty as slot-opinion state; the same text live and in calibration."""
    parts = [part.strip() for part in (charter, personality) if part.strip()]
    return "\n\n".join(parts) or "(no charter configured)"


def working_state_from_page(content: str) -> dict[str, Any]:
    """The state mapping of a working-state page (``## State`` fenced JSON)."""
    match = re.search(r"## State\s*```json\s*(.*?)\s*```", content, re.S)
    if match is None:
        raise ValueError("resident working-state page has no `## State` JSON block")
    state = json.loads(match.group(1))
    if not isinstance(state, dict):
        raise ValueError("resident working-state page `## State` is not a mapping")
    return state


@dataclass(frozen=True)
class SlotSelection:
    """Which selected slots go to the turn in full and which ride along as receipts."""

    turn: list[tuple[str, ResidentInboxSignal]]
    receipts: list[tuple[str, ResidentInboxSignal]] = field(default_factory=list)


class ResidentSlotAttention:
    """Judge inbox slots per slot, gate them when enabled, and compare with the turn."""

    def __init__(
        self,
        decisions: DecisionPort,
        *,
        policy: AttentionGatePolicy,
        gate_enabled: bool,
        max_observation_chars: int,
    ) -> None:
        if max_observation_chars <= 0:
            raise ValueError("max_observation_chars must be positive")
        if gate_enabled and policy.min_skip_probability is None:
            raise ValueError("the attention gate needs a fitted min_skip_probability")
        self._decisions = decisions
        self._policy = policy
        self._gate_enabled = gate_enabled
        self._max_observation_chars = max_observation_chars

    @property
    def policy(self) -> AttentionGatePolicy:
        return self._policy

    @property
    def gate_enabled(self) -> bool:
        return self._gate_enabled

    async def judge(
        self,
        rows: Sequence[tuple[str, ResidentInboxSignal]],
        *,
        mandate: str,
        working_state: Mapping[str, Any] | None,
    ) -> dict[str, SlotOpinion]:
        """One decision request per slot, concurrently; any failure raises."""
        opinions = await asyncio.gather(
            *(
                self._judge_one(ref, signal, mandate=mandate, working_state=working_state)
                for ref, signal in rows
            )
        )
        return {opinion.ref: opinion for opinion in opinions}

    async def _judge_one(
        self,
        ref: str,
        signal: ResidentInboxSignal,
        *,
        mandate: str,
        working_state: Mapping[str, Any] | None,
    ) -> SlotOpinion:
        result = await self._decisions.decide(
            self.state(ref, signal, mandate=mandate, working_state=working_state),
            QUESTIONS,
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
            raise TypeError("decision port answered resident slot-opinion questions mistyped")
        return SlotOpinion(
            ref=ref,
            last_archive_ref=signal.last_archive_ref,
            model=result.model,
            tier=tier.choice,
            tier_probabilities=dict(tier.probabilities),
            decision=decision.choice,
            decision_probabilities=dict(decision.probabilities),
            needs_investigation=needs_investigation.probability,
            eligible=gate_eligible(signal),
        )

    def state(
        self,
        ref: str,
        signal: ResidentInboxSignal,
        *,
        mandate: str,
        working_state: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        observation = self._bounded(slot_observation(ref, signal), what=f"inbox slot {ref}")
        state_text = (
            json.dumps(working_state, indent=2, sort_keys=True, ensure_ascii=False, default=str)
            if working_state
            else "(no working state recorded yet)"
        )
        return {
            "mandate": mandate,
            "working_state": self._bounded(state_text, what="resident working state"),
            "observation": observation,
        }

    def _bounded(self, text: str, *, what: str) -> str:
        if len(text) <= self._max_observation_chars:
            return text
        record_truncation(
            "decision.resident_first_opinion.max_observation_chars",
            what=f"{what} for the slot opinion",
            kept=self._max_observation_chars,
            total=len(text),
        )
        return text[: self._max_observation_chars]

    def select(
        self,
        rows: Sequence[tuple[str, ResidentInboxSignal]],
        opinions: Mapping[str, SlotOpinion],
    ) -> SlotSelection:
        """Split the selected slots by the gate; without the gate every slot gets the turn."""
        if not self._gate_enabled:
            return SlotSelection(turn=list(rows))
        turn: list[tuple[str, ResidentInboxSignal]] = []
        receipts: list[tuple[str, ResidentInboxSignal]] = []
        for ref, signal in rows:
            skip = self._policy.would_skip(opinions[ref])
            (receipts if skip else turn).append((ref, signal))
            get_observability().count(
                "ravn.decision.attention_gate.slots",
                attributes={
                    "ravn.decision.gate.outcome": "receipt" if skip else "turn",
                    "ravn.decision.gate.eligible": opinions[ref].eligible,
                },
                description="Inbox slots the attention gate sent to a turn or to a receipt.",
            )
        return SlotSelection(turn=turn, receipts=receipts)

    def compare(
        self,
        records: Mapping[str, Mapping[str, Any]],
        outcome: Mapping[str, Any] | None,
        *,
        outcome_valid: bool,
    ) -> None:
        """Record each slot's opinion beside the turn's own judgment."""
        telemetry = get_observability()
        turn_needed = self._policy.turn_needed(outcome, outcome_valid=outcome_valid)
        resident = {
            "tier": _vocabulary(outcome, "tier", VALKYRIE_ATTENTION_TIER_ORDER),
            "decision": _vocabulary(outcome, "decision", VALKYRIE_DECISION_ORDER),
        }
        for record in records.values():
            opinion = SlotOpinion.from_record(record)
            skip_probability = self._policy.skip_probability(opinion)
            would_skip = self._policy.would_skip(opinion)
            base: dict[str, Any] = {
                "ravn.decision.model": opinion.model,
                "ravn.decision.gate.eligible": opinion.eligible,
                "ravn.valkyrie.outcome_valid": outcome_valid,
            }
            if turn_needed is not None:
                base["ravn.decision.turn_needed"] = turn_needed
            if would_skip is not None:
                base["ravn.decision.gate.would_skip"] = would_skip
            for question, first in (("tier", opinion.tier), ("decision", opinion.decision)):
                attributes = {
                    **base,
                    "ravn.decision.question": question,
                    "ravn.decision.first_opinion": first,
                    "ravn.decision.resident": resident[question],
                }
                if resident[question] is not None:
                    attributes["ravn.decision.agreed"] = first == resident[question]
                telemetry.count("ravn.decision.slot_opinion.comparisons", attributes=attributes)
            telemetry.record(
                "ravn.decision.slot_opinion.skip_probability",
                skip_probability,
                attributes=base,
            )
            telemetry.event(
                "ravn.decision.slot_opinion",
                attributes={**base, "ravn.resident.inbox_ref": opinion.ref},
                content={
                    "opinion": opinion.to_record(),
                    "skip_probability": skip_probability,
                    "resident": resident,
                    "turn_needed": turn_needed,
                },
            )


def receipt_lines(
    receipts: Sequence[tuple[str, ResidentInboxSignal]],
    opinions: Mapping[str, SlotOpinion],
    policy: AttentionGatePolicy,
) -> list[str]:
    """The receipt block for slots the gate kept out of turns of their own."""
    if not receipts:
        return []
    lines = [
        "",
        "Since you last thought — slots a first opinion judged routine, so they had no turn of "
        "their own. You are acknowledging them with this turn; say so if any judgment is wrong:",
    ]
    for ref, signal in receipts:
        opinion = opinions[ref]
        lines.append(
            f"- inbox_ref={ref}; source={signal.source}; kind={signal.kind}; "
            f"observations={signal.observation_count} "
            f"({signal.first_observed_at or signal.observed_at} to {signal.observed_at}); "
            f"first opinion {opinion.model}: tier={opinion.tier}, decision={opinion.decision}, "
            f"routine probability={policy.skip_probability(opinion):.2f}; "
            f"summary={signal.summary}"
        )
        lines.extend(aggregate_summary_lines(signal.aggregate))
    return lines


def _vocabulary(
    outcome: Mapping[str, Any] | None, key: str, vocabulary: tuple[str, ...]
) -> str | None:
    value = str((outcome or {}).get(key) or "").strip()
    return value if value in vocabulary else None


def _span_seconds(first: str, last: str) -> int | None:
    try:
        return int((datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds())
    except (TypeError, ValueError):
        return None
