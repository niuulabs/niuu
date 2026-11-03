"""Offline calibration of the per-slot attention gate against a resident's own history.

``docs/design/system-one-decision-models.md`` §5 step 2: before any slot is skipped, replay the
slots this resident already judged and measure how a decision model's opinion would have gated
them. Every judged slot in the inbox's ``processed/`` area names the turn that acknowledged it
(``recorded by resident case <case> turn <n>``), and that turn record holds the resident's own
tier and decision. The slot is replayed through exactly the state the live opinion uses — the
slot as judged, the charter, and the working state the resident held *before* that turn — so
the replay measures the gate that would run, not an approximation of it.

The label is the same conservative one the live comparison uses (``AttentionGatePolicy``): a
turn that concluded routine means every slot in it could have been skipped; any other
conclusion means each needed the turn. The sweep then reports, at every skip probability that
actually occurs in the data, how many eligible slots the gate would have skipped (coverage) and
how many of those the resident had judged worth a turn (misses). Thresholds are fitted from
this, never from vendor benchmarks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from ravn.domain.models import RESIDENT_HOME_TRIGGER
from ravn.domain.resident_continuation import resident_working_state_from_outcome
from ravn.domain.resident_state import ResidentStatePort
from ravn.resident_attention import ResidentSlotAttention
from ravn.resident_inbox import ResidentInboxStatus
from ravn.resident_timeline import turn_from_content, turn_outcome_fields

_TURN_SEGMENT = "/turns/"
_JUDGED_BY = re.compile(r"recorded by resident case (?P<case>\S+) turn (?P<turn>\d+)")


@dataclass(frozen=True)
class JudgedTurn:
    """One durable resident turn, with the working state it started from."""

    case_id: str
    turn_index: int
    triggered_by: str
    updated_at: str
    outcome: Mapping[str, Any]
    prior_working_state: Mapping[str, Any] | None


@dataclass(frozen=True)
class CalibrationRow:
    ref: str
    case_id: str
    turn_index: int
    observations: int
    eligible: bool
    model: str
    tier: str
    decision: str
    needs_investigation: float
    skip_probability: float
    turn_needed: bool | None
    resident_tier: str
    resident_decision: str


@dataclass(frozen=True)
class SweepPoint:
    threshold: float
    skipped: int
    coverage: float
    misses: int
    miss_rate: float


@dataclass
class CalibrationReport:
    models: list[str] = field(default_factory=list)
    judged_slots: int = 0
    replayed_slots: int = 0
    #: Slots whose turn record no longer exists (pruned case) or never named one.
    unmatched_slots: int = 0
    #: Slots judged by something other than a home turn (pre-coalescing per-event tasks).
    not_home_turn_slots: int = 0
    eligible_slots: int = 0
    labelled_eligible_slots: int = 0
    rows: list[CalibrationRow] = field(default_factory=list)
    sweep: list[SweepPoint] = field(default_factory=list)
    #: Lowest threshold with no misses: the most coverage the data supports without a miss.
    zero_miss_threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def load_judged_turns(state: ResidentStatePort) -> dict[tuple[str, int], JudgedTurn]:
    """Every durable turn by (case, turn), each with the working state preceding it."""
    parsed: list[tuple[str, Any, dict[str, Any]]] = []
    for ref in sorted(ref for ref in await state.list_refs("") if _TURN_SEGMENT in ref):
        entry = await state.read(ref)
        if entry is None:
            continue
        turn = turn_from_content(ref, entry.content)
        if turn is None:
            continue
        parsed.append((turn.updated_at, turn, turn_outcome_fields(entry.content)))
    parsed.sort(key=lambda item: (item[0], item[1].turn_index))
    judged: dict[tuple[str, int], JudgedTurn] = {}
    working_state: Mapping[str, Any] | None = None
    for updated_at, turn, fields in parsed:
        judged[(turn.case_id, turn.turn_index)] = JudgedTurn(
            case_id=turn.case_id,
            turn_index=turn.turn_index,
            triggered_by=turn.triggered_by,
            updated_at=updated_at,
            outcome=fields,
            prior_working_state=working_state,
        )
        written = resident_working_state_from_outcome(fields)
        if written is not None:
            working_state = written
    return judged


async def calibrate(
    *,
    state: ResidentStatePort,
    inbox: Any,
    attention: ResidentSlotAttention,
    mandate: str,
    limit: int,
) -> CalibrationReport:
    """Replay up to *limit* judged slots (newest first) and sweep the gate threshold."""
    turns = await load_judged_turns(state)
    slots = await inbox.list_signals(status=ResidentInboxStatus.REMEMBERED.value, limit=limit)
    report = CalibrationReport(judged_slots=len(slots))
    models: set[str] = set()
    policy = attention.policy
    for ref, slot in slots:
        match = _JUDGED_BY.search(slot.reason)
        turn = turns.get((match["case"], int(match["turn"]))) if match else None
        if turn is None:
            report.unmatched_slots += 1
            continue
        if turn.triggered_by != RESIDENT_HOME_TRIGGER:
            report.not_home_turn_slots += 1
            continue
        opinion = (
            await attention.judge(
                [(ref, slot)],
                mandate=mandate,
                working_state=turn.prior_working_state,
            )
        )[ref]
        models.add(opinion.model)
        report.rows.append(
            CalibrationRow(
                ref=ref,
                case_id=turn.case_id,
                turn_index=turn.turn_index,
                observations=slot.observation_count,
                eligible=opinion.eligible,
                model=opinion.model,
                tier=opinion.tier,
                decision=opinion.decision,
                needs_investigation=opinion.needs_investigation,
                skip_probability=policy.skip_probability(opinion),
                # Only a valid outcome acknowledges a slot, so every matched turn is valid.
                turn_needed=policy.turn_needed(turn.outcome, outcome_valid=True),
                resident_tier=str(turn.outcome.get("tier") or ""),
                resident_decision=str(turn.outcome.get("decision") or ""),
            )
        )
    report.models = sorted(models)
    report.replayed_slots = len(report.rows)
    report.eligible_slots = sum(row.eligible for row in report.rows)
    labelled = [row for row in report.rows if row.eligible and row.turn_needed is not None]
    report.labelled_eligible_slots = len(labelled)
    report.sweep = sweep(labelled)
    zero_miss = [point.threshold for point in report.sweep if point.misses == 0]
    report.zero_miss_threshold = min(zero_miss) if zero_miss else None
    return report


def sweep(rows: Sequence[CalibrationRow]) -> list[SweepPoint]:
    """Coverage and misses at every skip probability that occurs in *rows*."""
    if not rows:
        return []
    points: list[SweepPoint] = []
    for threshold in sorted({row.skip_probability for row in rows}, reverse=True):
        skipped = [row for row in rows if row.skip_probability >= threshold]
        misses = sum(1 for row in skipped if row.turn_needed)
        points.append(
            SweepPoint(
                threshold=threshold,
                skipped=len(skipped),
                coverage=len(skipped) / len(rows),
                misses=misses,
                miss_rate=misses / len(skipped),
            )
        )
    return points
