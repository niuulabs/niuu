"""Domain models for resident long-horizon work management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from ravn.domain.resident_continuation import ResidentBudgetSnapshot


class ResidentObjectiveKind(StrEnum):
    RESEARCH = "research"
    SPECIFICATION = "specification"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    TOOL_BUILDING = "tool_building"
    CREATIVE_EXPLORATION = "creative_exploration"
    OPERATOR_QUESTION = "operator_question"
    REVIEW = "review"
    CONSOLIDATION = "consolidation"
    REMOTE_EXECUTION = "remote_execution"


class ResidentObjectiveStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    NEEDS_OPERATOR = "needs_operator"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ResidentPortfolioDecisionKind(StrEnum):
    CONTINUE = "continue"
    ASK_OPERATOR = "ask_operator"
    SLEEP = "sleep"
    STOP = "stop"


@dataclass(frozen=True)
class ResidentObjective:
    """Durable long-horizon resident objective."""

    id: str
    title: str
    purpose: str
    serves_mandate_because: str
    expected_outcome: str
    proof_criteria: tuple[str, ...]
    kind: str = ResidentObjectiveKind.RESEARCH.value
    dependencies: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    risk_boundaries: tuple[str, ...] = ()
    budget_estimate: str = "small"
    priority_score: int = 0
    priority_band: str = "normal"
    priority_rationale: str = ""
    status: str = ResidentObjectiveStatus.CANDIDATE.value
    source_evidence: tuple[str, ...] = ()
    reasoning: str = ""
    pending_question: str = ""
    proof_progress: tuple[str, ...] = ()
    artifact_links: tuple[str, ...] = ()
    wake_links: tuple[str, ...] = ()
    workstream_links: tuple[str, ...] = ()
    consolidation_links: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    superseded_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_advanced_at: datetime | None = None
    last_reviewed_at: datetime | None = None

    def with_status(self, status: str) -> ResidentObjective:
        return self.with_updates(status=status)

    def with_updates(self, **updates: object) -> ResidentObjective:
        values = {
            "id": self.id,
            "title": self.title,
            "purpose": self.purpose,
            "serves_mandate_because": self.serves_mandate_because,
            "expected_outcome": self.expected_outcome,
            "proof_criteria": self.proof_criteria,
            "kind": self.kind,
            "dependencies": self.dependencies,
            "required_capabilities": self.required_capabilities,
            "risk_boundaries": self.risk_boundaries,
            "budget_estimate": self.budget_estimate,
            "priority_score": self.priority_score,
            "priority_band": self.priority_band,
            "priority_rationale": self.priority_rationale,
            "status": self.status,
            "source_evidence": self.source_evidence,
            "reasoning": self.reasoning,
            "pending_question": self.pending_question,
            "proof_progress": self.proof_progress,
            "artifact_links": self.artifact_links,
            "wake_links": self.wake_links,
            "workstream_links": self.workstream_links,
            "consolidation_links": self.consolidation_links,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "created_at": self.created_at,
            "updated_at": datetime.now(UTC),
            "last_advanced_at": self.last_advanced_at,
            "last_reviewed_at": self.last_reviewed_at,
        }
        values.update(updates)
        return ResidentObjective(**values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ResidentPortfolio:
    """Compact portfolio of long-horizon resident work."""

    mandate: str
    objectives: tuple[ResidentObjective, ...] = ()
    domain_model_ref: str = ""
    wake_record_links: tuple[str, ...] = ()
    workstream_links: tuple[str, ...] = ()
    artifact_links: tuple[str, ...] = ()
    consolidation_links: tuple[str, ...] = ()
    decision_history: tuple[str, ...] = ()
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def with_objectives(
        self,
        objectives: tuple[ResidentObjective, ...],
        *,
        decision_history: tuple[str, ...] | None = None,
        wake_record_links: tuple[str, ...] | None = None,
        workstream_links: tuple[str, ...] | None = None,
        artifact_links: tuple[str, ...] | None = None,
        consolidation_links: tuple[str, ...] | None = None,
        domain_model_ref: str | None = None,
    ) -> ResidentPortfolio:
        return ResidentPortfolio(
            mandate=self.mandate,
            objectives=objectives,
            domain_model_ref=domain_model_ref
            if domain_model_ref is not None
            else self.domain_model_ref,
            wake_record_links=wake_record_links
            if wake_record_links is not None
            else self.wake_record_links,
            workstream_links=workstream_links
            if workstream_links is not None
            else self.workstream_links,
            artifact_links=artifact_links if artifact_links is not None else self.artifact_links,
            consolidation_links=consolidation_links
            if consolidation_links is not None
            else self.consolidation_links,
            decision_history=decision_history
            if decision_history is not None
            else self.decision_history,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True)
class ResidentPortfolioRun:
    mandate: str
    portfolio_ref: str
    portfolio: ResidentPortfolio
    discovered_objectives: tuple[ResidentObjective, ...]
    selected_objectives: tuple[ResidentObjective, ...]
    advanced_objectives: tuple[ResidentObjective, ...]
    decision: ResidentPortfolioDecisionKind
    decision_reason: str
    budget: ResidentBudgetSnapshot


class ResidentWorkItemBackend(Protocol):
    """Persistence boundary for resident portfolio/objective work items."""

    async def read_portfolio(self, mandate: str) -> ResidentPortfolio | None:
        """Load a compact resident portfolio if one exists."""

    async def write_portfolio(self, portfolio: ResidentPortfolio) -> str:
        """Persist the compact resident portfolio and return its reference."""

    async def list_objectives(self, mandate: str) -> list[ResidentObjective]:
        """Return durable resident objectives for the mandate."""

    async def write_objective(self, objective: ResidentObjective) -> str:
        """Persist one resident objective and return its reference."""

    async def append_decision(self, mandate: str, entry: str) -> str:
        """Persist an audit/decision entry and return its reference."""

    async def list_refs(self, prefix: str) -> list[str]:
        """List backend references under a prefix for portfolio linking."""
