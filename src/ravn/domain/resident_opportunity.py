"""Domain models for resident opportunity generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from ravn.domain.resident_expert import ResidentDomainModel
from ravn.domain.resident_portfolio import ResidentObjective


class ResidentOpportunityStage(StrEnum):
    IDEA = "idea"
    HYPOTHESIS = "hypothesis"
    EXPERIMENT = "experiment"
    COMMITTED_WORK = "committed_work"
    SUPPRESSED = "suppressed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ResidentOpportunitySignal:
    """Evidence signal that may imply a useful resident opportunity."""

    id: str
    source: str
    kind: str
    summary: str
    evidence_ref: str
    themes: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    confidence: float = 0.5


@dataclass(frozen=True)
class ResidentOpportunityScore:
    """Comparable opportunity score dimensions."""

    expected_value: int
    risk: int
    cost: int
    novelty: int
    feasibility: int
    operator_alignment: int
    total: int


@dataclass(frozen=True)
class ResidentOpportunityCandidate:
    """A proposed opportunity with evidence, risk, and a safe experiment."""

    id: str
    title: str
    summary: str
    stage: str
    rationale: str
    evidence: tuple[str, ...]
    assumptions: tuple[str, ...]
    risks: tuple[str, ...]
    safe_next_experiment: str
    score: ResidentOpportunityScore
    duplicate_key: str
    source_signal_ids: tuple[str, ...] = ()
    operator_question: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def with_stage(self, stage: str) -> ResidentOpportunityCandidate:
        return ResidentOpportunityCandidate(
            id=self.id,
            title=self.title,
            summary=self.summary,
            stage=stage,
            rationale=self.rationale,
            evidence=self.evidence,
            assumptions=self.assumptions,
            risks=self.risks,
            safe_next_experiment=self.safe_next_experiment,
            score=self.score,
            duplicate_key=self.duplicate_key,
            source_signal_ids=self.source_signal_ids,
            operator_question=self.operator_question,
            created_at=self.created_at,
        )


@dataclass(frozen=True)
class ResidentOpportunityReport:
    """Result of one bounded resident opportunity-generation pass."""

    mandate: str
    signals: tuple[ResidentOpportunitySignal, ...]
    candidates: tuple[ResidentOpportunityCandidate, ...]
    selected_opportunities: tuple[ResidentOpportunityCandidate, ...]
    suppressed_opportunities: tuple[ResidentOpportunityCandidate, ...]
    created_objectives: tuple[ResidentObjective, ...]
    persisted_refs: tuple[str, ...]
    duplicate_notes: tuple[str, ...]
    budget_notes: str
    final_suggested_next_action: str


class ResidentOpportunitySourcePort(Protocol):
    """Boundary for collecting opportunity signals from memory/research sources."""

    async def collect(
        self,
        *,
        mandate: str,
        domain_model: ResidentDomainModel | None,
        objectives: tuple[ResidentObjective, ...],
        limit: int,
    ) -> tuple[ResidentOpportunitySignal, ...]:
        """Return opportunity evidence signals visible to the resident."""
