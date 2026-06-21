"""Domain models for resident long-horizon work management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from ravn.domain.operator_contact import OperatorContactResult
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


class ResidentPortfolioStewardActionKind(StrEnum):
    REPAIR = "repair"
    ADVANCE = "advance"
    ASK_OPERATOR = "ask_operator"
    SLEEP = "sleep"
    STOP = "stop"


class ResidentPortfolioValidationSeverity(StrEnum):
    ISSUE = "issue"
    WARNING = "warning"


class ResidentDelegationStatus(StrEnum):
    CANDIDATE = "candidate"
    LAUNCHED = "launched"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_OPERATOR = "needs_operator"
    UNAVAILABLE = "unavailable"


class ResidentDelegationReviewDecision(StrEnum):
    COMPLETE = "complete"
    NEEDS_FOLLOW_UP = "needs_follow_up"
    BLOCKED = "blocked"
    RETRY = "retry"
    ASK_OPERATOR = "ask_operator"


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


@dataclass(frozen=True)
class ResidentPortfolioValidationFinding:
    severity: ResidentPortfolioValidationSeverity
    code: str
    message: str
    objective_id: str = ""
    ref: str = ""


@dataclass(frozen=True)
class ResidentObjectiveDryRunPreview:
    objective_id: str
    title: str
    status: str
    priority_score: int
    priority_rationale: str
    dependency_readiness: str
    budget_notes: str
    risk_notes: str


@dataclass(frozen=True)
class ResidentPortfolioValidationReport:
    verdict: str
    issues: tuple[ResidentPortfolioValidationFinding, ...]
    warnings: tuple[ResidentPortfolioValidationFinding, ...]
    selected_objective: ResidentObjectiveDryRunPreview | None
    eligible_objectives: tuple[ResidentObjectiveDryRunPreview, ...]
    blocked_objectives: tuple[ResidentObjectiveDryRunPreview, ...]
    operator_needed_objectives: tuple[ResidentObjectiveDryRunPreview, ...]
    skipped_reasons: tuple[str, ...]
    objective_counts_by_status: tuple[tuple[str, int], ...]
    dependency_graph_summary: str
    stale_duplicate_superseded_hints: tuple[str, ...]
    suggested_safe_next_action: str
    mutated_state: bool = False


@dataclass(frozen=True)
class ResidentPortfolioRepairRecord:
    code: str
    objective_id: str
    action: str
    reason: str
    before: str = ""
    after: str = ""
    ref: str = ""


@dataclass(frozen=True)
class ResidentPortfolioStewardPassReport:
    pass_number: int
    validation_before: ResidentPortfolioValidationReport
    validation_after: ResidentPortfolioValidationReport
    repairs_attempted: tuple[ResidentPortfolioRepairRecord, ...]
    repairs_skipped: tuple[ResidentPortfolioRepairRecord, ...]
    selected_objective: ResidentObjectiveDryRunPreview | None
    action_taken: ResidentPortfolioStewardActionKind
    advanced_objective_id: str = ""
    new_follow_up_objectives: tuple[ResidentObjective, ...] = ()
    operator_questions: tuple[str, ...] = ()
    persisted_refs: tuple[str, ...] = ()
    budget: ResidentBudgetSnapshot = field(default_factory=ResidentBudgetSnapshot)
    final_suggested_next_action: str = ""


@dataclass(frozen=True)
class ResidentPortfolioStewardRun:
    mandate: str
    passes: tuple[ResidentPortfolioStewardPassReport, ...]
    final_action: ResidentPortfolioStewardActionKind
    final_suggested_next_action: str
    budget: ResidentBudgetSnapshot = field(default_factory=ResidentBudgetSnapshot)


@dataclass(frozen=True)
class ResidentCapabilityGap:
    id: str
    capability: str
    summary: str
    source_objective_id: str = ""
    source_evidence: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    risk_boundaries: tuple[str, ...] = ()
    blocked_dependencies: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ResidentCapabilityOption:
    id: str
    title: str
    summary: str
    required_tools: tuple[str, ...] = ()
    required_workflows: tuple[str, ...] = ()
    required_adapters: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    approval_required: bool = False
    safe_next_experiment: str = ""
    evidence: tuple[str, ...] = ()
    configuration: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResidentCapabilityDiscoveryResult:
    gap: ResidentCapabilityGap
    capability_summary: str
    why_it_matters: str
    known_constraints: tuple[str, ...]
    candidate_options: tuple[ResidentCapabilityOption, ...]
    recommended_option_id: str
    recommended_safe_next_experiment: str
    unresolved_questions: tuple[str, ...] = ()
    budget_notes: str = "bounded local discovery"
    existing_capabilities: tuple[str, ...] = ()
    duplicate_check_notes: tuple[str, ...] = ()
    research_evidence: tuple[str, ...] = ()
    configuration_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResidentCapabilityDiscoveryReport:
    mandate: str
    selected_gap: ResidentCapabilityGap | None
    discovery_result: ResidentCapabilityDiscoveryResult | None
    created_or_updated_objectives: tuple[ResidentObjective, ...]
    operator_questions: tuple[str, ...]
    persisted_refs: tuple[str, ...]
    budget_notes: str
    final_suggested_next_action: str


@dataclass(frozen=True)
class ResidentWorkerBrief:
    id: str
    mandate: str
    objective_id: str
    objective_title: str
    desired_outcome: str
    proof_criteria: tuple[str, ...]
    evidence: tuple[str, ...] = ()
    artifact_links: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risk_boundaries: tuple[str, ...] = ()
    expected_output_shape: str = ""


@dataclass(frozen=True)
class ResidentExecutionSession:
    session_id: str
    status: str
    backend_name: str = "local"
    summary: str = ""


@dataclass(frozen=True)
class ResidentExecutionResult:
    session_id: str
    status: str
    summary: str
    output_refs: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    follow_up_suggestions: tuple[str, ...] = ()
    blocked_reason: str = ""
    usage: ResidentBudgetSnapshot = field(default_factory=ResidentBudgetSnapshot)


@dataclass(frozen=True)
class ResidentDelegationRecord:
    id: str
    source_objective_id: str
    backend_session_id: str
    backend_name: str
    brief: ResidentWorkerBrief
    status: str
    reason: str
    risk_boundaries: tuple[str, ...] = ()
    result_refs: tuple[str, ...] = ()
    follow_up_objective_refs: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def with_updates(self, **updates: object) -> ResidentDelegationRecord:
        values = {
            "id": self.id,
            "source_objective_id": self.source_objective_id,
            "backend_session_id": self.backend_session_id,
            "backend_name": self.backend_name,
            "brief": self.brief,
            "status": self.status,
            "reason": self.reason,
            "risk_boundaries": self.risk_boundaries,
            "result_refs": self.result_refs,
            "follow_up_objective_refs": self.follow_up_objective_refs,
            "created_at": self.created_at,
            "updated_at": datetime.now(UTC),
        }
        values.update(updates)
        return ResidentDelegationRecord(**values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ResidentDelegationReview:
    id: str
    delegation_id: str
    source_objective_id: str
    result_session_id: str
    decision: str
    reason: str
    proof_criteria_checked: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    follow_up_suggestions: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentDelegationReport:
    mandate: str
    selected_objectives: tuple[ResidentObjective, ...]
    created_delegations: tuple[ResidentDelegationRecord, ...]
    skipped_or_gated_objectives: tuple[ResidentObjective, ...]
    observed_results: tuple[ResidentExecutionResult, ...]
    reviews: tuple[ResidentDelegationReview, ...]
    updated_objectives: tuple[ResidentObjective, ...]
    created_follow_up_objectives: tuple[ResidentObjective, ...]
    operator_questions: tuple[str, ...]
    persisted_refs: tuple[str, ...]
    final_suggested_next_action: str


@dataclass(frozen=True)
class ResidentAutonomyCycleReport:
    cycle_number: int
    delegation_report: ResidentDelegationReport
    selected_objectives: tuple[ResidentObjective, ...]
    review_decisions: tuple[ResidentDelegationReview, ...]
    persisted_refs: tuple[str, ...]
    operator_questions: tuple[str, ...]
    final_suggested_next_action: str
    operator_contacts: tuple[OperatorContactResult, ...] = ()


@dataclass(frozen=True)
class ResidentAutonomyRun:
    mandate: str
    cycles: tuple[ResidentAutonomyCycleReport, ...]
    persisted_refs: tuple[str, ...]
    operator_questions: tuple[str, ...]
    final_suggested_next_action: str
    operator_contacts: tuple[OperatorContactResult, ...] = ()


class CapabilityDiscoveryPort(Protocol):
    """Boundary for researching/discovering ways to close a capability gap."""

    async def discover(
        self,
        mandate: str,
        gap: ResidentCapabilityGap,
    ) -> ResidentCapabilityDiscoveryResult:
        """Return bounded discovery results for a selected capability gap."""


class ResidentExecutionPort(Protocol):
    """Backend-neutral boundary for delegated resident execution."""

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        """Launch a bounded delegated work session."""

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        """Read current delegated work session status."""

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        """Read delegated work output/result when available."""

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        """Cancel or mark a delegated work session unavailable."""


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

    async def write_capability_discovery(
        self,
        discovery_id: str,
        content: str,
    ) -> str:
        """Persist a resident capability discovery artifact and return its reference."""

    async def list_delegations(self, mandate: str) -> list[ResidentDelegationRecord]:
        """Return resident delegation records."""

    async def write_delegation(self, delegation: ResidentDelegationRecord) -> str:
        """Persist one resident delegation record and return its reference."""

    async def write_delegation_result(
        self,
        delegation_id: str,
        result: ResidentExecutionResult,
        content: str,
    ) -> str:
        """Persist delegated execution output and return its reference."""

    async def write_delegation_review(
        self,
        review: ResidentDelegationReview,
        content: str,
    ) -> str:
        """Persist delegated execution review output and return its reference."""

    async def write_operator_contact(self, result: OperatorContactResult) -> str:
        """Persist an operator wait-state/audit record and return its reference."""
