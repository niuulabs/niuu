"""Domain models for the resident domain-expert loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from ravn.domain.resident_continuation import (
    ResidentBudgetSnapshot,
    ResidentPolicyObservation,
)


class ExpertLoopDecisionKind(StrEnum):
    CONTINUE = "continue"
    ASK_OPERATOR = "ask_operator"
    SLEEP = "sleep"
    STOP = "stop"


class WorkProductKind(StrEnum):
    PRD = "prd"
    SRD = "srd"
    TECHNICAL_DESIGN = "technical_design"
    RESEARCH_BRIEF = "research_brief"
    OPERATING_PROCEDURE = "operating_procedure"
    CAPABILITY_EVALUATION = "capability_evaluation"
    EXPERIMENT_PLAN = "experiment_plan"
    IMPLEMENTATION_PLAN = "implementation_plan"
    VERIFICATION_PLAN = "verification_plan"
    RETROSPECTIVE = "retrospective"
    DOMAIN_BRIEF = "domain_brief"


class ResidentWorkstreamKind(StrEnum):
    LOCAL_FOLLOW_UP = "local_follow_up"
    RESEARCH = "research"
    SPECIFICATION = "specification"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    WORKFLOW_BUILDING = "workflow_building"
    REMOTE_EXECUTION = "remote_execution"


class ResidentWorkstreamStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    NEEDS_OPERATOR = "needs_operator"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ExpertArtifact:
    """A durable work product created by the resident or a workstream."""

    title: str
    kind: str
    path: str
    purpose: str
    summary: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentWorkstream:
    """Self-authored unit of domain-expert work."""

    id: str
    title: str
    purpose: str
    serves: str
    expected_artifact: str
    kind: str = ResidentWorkstreamKind.LOCAL_FOLLOW_UP.value
    required_capabilities: tuple[str, ...] = ()
    risk_boundaries: tuple[str, ...] = ()
    budget_estimate: str = "small"
    status: str = ResidentWorkstreamStatus.PROPOSED.value
    parent_domain_model_ref: str = ""
    selected_work_product: str = WorkProductKind.DOMAIN_BRIEF.value
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def activate(self) -> ResidentWorkstream:
        return self.with_status(ResidentWorkstreamStatus.ACTIVE.value)

    def with_status(self, status: str) -> ResidentWorkstream:
        return ResidentWorkstream(
            id=self.id,
            title=self.title,
            purpose=self.purpose,
            serves=self.serves,
            expected_artifact=self.expected_artifact,
            kind=self.kind,
            required_capabilities=self.required_capabilities,
            risk_boundaries=self.risk_boundaries,
            budget_estimate=self.budget_estimate,
            status=status,
            parent_domain_model_ref=self.parent_domain_model_ref,
            selected_work_product=self.selected_work_product,
            created_at=self.created_at,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True)
class ResidentDomainModel:
    """Compact living model of the resident's domain."""

    mandate: str
    current_understanding: str = ""
    hypotheses: tuple[str, ...] = ()
    known_facts: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    domain_risks: tuple[str, ...] = ()
    resident_decisions: tuple[str, ...] = ()
    failure_notes: tuple[str, ...] = ()
    open_threads: tuple[str, ...] = ()
    memory_hygiene_notes: tuple[str, ...] = ()
    opportunities: tuple[str, ...] = ()
    capability_gaps: tuple[str, ...] = ()
    active_workstreams: tuple[ResidentWorkstream, ...] = ()
    recent_outcomes: tuple[str, ...] = ()
    learned_policy_observations: tuple[ResidentPolicyObservation, ...] = ()
    artifacts: tuple[ExpertArtifact, ...] = ()
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def with_workstreams(
        self,
        workstreams: tuple[ResidentWorkstream, ...],
    ) -> ResidentDomainModel:
        return ResidentDomainModel(
            mandate=self.mandate,
            current_understanding=self.current_understanding,
            hypotheses=self.hypotheses,
            known_facts=self.known_facts,
            open_questions=self.open_questions,
            domain_risks=self.domain_risks,
            resident_decisions=self.resident_decisions,
            failure_notes=self.failure_notes,
            open_threads=self.open_threads,
            memory_hygiene_notes=self.memory_hygiene_notes,
            opportunities=self.opportunities,
            capability_gaps=self.capability_gaps,
            active_workstreams=workstreams,
            recent_outcomes=self.recent_outcomes,
            learned_policy_observations=self.learned_policy_observations,
            artifacts=self.artifacts,
            updated_at=datetime.now(UTC),
        )

    def with_consolidation(
        self,
        *,
        workstreams: tuple[ResidentWorkstream, ...] | None = None,
        recent_outcomes: tuple[str, ...] | None = None,
        known_facts: tuple[str, ...] | None = None,
        resident_decisions: tuple[str, ...] | None = None,
        failure_notes: tuple[str, ...] | None = None,
        open_threads: tuple[str, ...] | None = None,
        memory_hygiene_notes: tuple[str, ...] | None = None,
        capability_gaps: tuple[str, ...] | None = None,
        learned_policy_observations: tuple[ResidentPolicyObservation, ...] | None = None,
        artifacts: tuple[ExpertArtifact, ...] | None = None,
    ) -> ResidentDomainModel:
        return ResidentDomainModel(
            mandate=self.mandate,
            current_understanding=self.current_understanding,
            hypotheses=self.hypotheses,
            known_facts=known_facts if known_facts is not None else self.known_facts,
            open_questions=self.open_questions,
            domain_risks=self.domain_risks,
            resident_decisions=resident_decisions
            if resident_decisions is not None
            else self.resident_decisions,
            failure_notes=failure_notes if failure_notes is not None else self.failure_notes,
            open_threads=open_threads if open_threads is not None else self.open_threads,
            memory_hygiene_notes=memory_hygiene_notes
            if memory_hygiene_notes is not None
            else self.memory_hygiene_notes,
            opportunities=self.opportunities,
            capability_gaps=capability_gaps
            if capability_gaps is not None
            else self.capability_gaps,
            active_workstreams=workstreams if workstreams is not None else self.active_workstreams,
            recent_outcomes=recent_outcomes
            if recent_outcomes is not None
            else self.recent_outcomes,
            learned_policy_observations=learned_policy_observations
            if learned_policy_observations is not None
            else self.learned_policy_observations,
            artifacts=artifacts if artifacts is not None else self.artifacts,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True)
class WorkstreamExecutionResult:
    """Outcome of advancing one workstream through an execution backend."""

    workstream_id: str
    status: str
    summary: str
    artifact_refs: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    lessons: tuple[str, ...] = ()
    capability_gaps: tuple[str, ...] = ()
    policy_observations: tuple[ResidentPolicyObservation, ...] = ()
    usage: Any | None = None


@dataclass(frozen=True)
class ExpertLoopDecision:
    kind: ExpertLoopDecisionKind
    reason: str
    workstream: ResidentWorkstream | None = None
    question: str = ""


@dataclass(frozen=True)
class ResidentDomainExpertRun:
    mandate: str
    domain_model_ref: str
    domain_model: ResidentDomainModel
    workstreams: tuple[ResidentWorkstream, ...]
    artifacts: tuple[ExpertArtifact, ...]
    execution_results: tuple[WorkstreamExecutionResult, ...]
    decisions: tuple[ExpertLoopDecision, ...]
    budget: ResidentBudgetSnapshot

    @property
    def final_decision(self) -> ExpertLoopDecision | None:
        return self.decisions[-1] if self.decisions else None


class ResidentDomainExpertMemoryPort(Protocol):
    """Persistence boundary for domain-expert state."""

    async def read_domain_model(self, mandate: str) -> ResidentDomainModel | None:
        """Load the current compact domain model, if one exists."""

    async def write_domain_model(self, model: ResidentDomainModel) -> str:
        """Persist the compact domain model and return its reference."""

    async def list_workstreams(self, domain_model_ref: str) -> list[ResidentWorkstream]:
        """Return known workstreams for the domain model."""

    async def write_workstream(self, workstream: ResidentWorkstream) -> str:
        """Persist one workstream and return its reference."""

    async def write_artifact(self, artifact: ExpertArtifact, content: str) -> str:
        """Persist an expert artifact and return its reference."""

    async def write_consolidation(
        self,
        model: ResidentDomainModel,
        result: WorkstreamExecutionResult,
    ) -> str:
        """Persist a workstream consolidation summary."""


class WorkstreamExecutionPort(Protocol):
    """Backend-neutral boundary for advancing resident workstreams."""

    async def advance(
        self,
        workstream: ResidentWorkstream,
        *,
        domain_model: ResidentDomainModel,
    ) -> WorkstreamExecutionResult:
        """Advance one workstream and return a structured result."""
