"""Domain models for the Ting saga coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from ting.domain.exceptions import InvalidStateTransitionError

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SagaStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class PhaseStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    GATED = "GATED"
    COMPLETE = "COMPLETE"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    REVIEW = "REVIEW"
    ESCALATED = "ESCALATED"
    MERGED = "MERGED"
    FAILED = "FAILED"


class ConfidenceEventType(StrEnum):
    CI_PASS = "ci_pass"
    CI_FAIL = "ci_fail"
    SCOPE_BREACH = "scope_breach"
    RETRY = "retry"
    HUMAN_REJECT = "human_reject"
    HUMAN_APPROVED = "human_approved"
    AUTO_APPROVED = "auto_approved"
    PR_CONFLICT = "pr_conflict"
    PR_MERGEABLE = "pr_mergeable"
    MESSAGE_SENT = "message_sent"
    REVIEWER_SCORE = "reviewer_score"


class WorkflowScope(StrEnum):
    SYSTEM = "system"
    USER = "user"


class WorkflowCampaignStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.QUEUED}),
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset({RunStatus.REVIEW, RunStatus.MERGED, RunStatus.FAILED}),
    RunStatus.REVIEW: frozenset(
        {
            RunStatus.PENDING,
            RunStatus.QUEUED,
            RunStatus.ESCALATED,
            RunStatus.MERGED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.ESCALATED: frozenset({RunStatus.QUEUED, RunStatus.MERGED, RunStatus.FAILED}),
    RunStatus.MERGED: frozenset(),
    RunStatus.FAILED: frozenset({RunStatus.QUEUED}),
}


def validate_transition(current: RunStatus, target: RunStatus) -> None:
    """Validate a run state transition, raising on invalid moves."""
    allowed = RUN_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidStateTransitionError(current, target)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Saga:
    id: UUID
    tracker_id: str
    tracker_type: str
    slug: str
    name: str
    repos: list[str]
    feature_branch: str
    status: SagaStatus
    confidence: float
    created_at: datetime
    base_branch: str
    repo_branches: dict[str, str] = field(default_factory=dict)
    owner_id: str = ""
    workflow_id: UUID | None = None
    workflow_version: str | None = None
    workflow_snapshot: dict[str, Any] | None = None
    instance_id: str | None = None
    target_tags: list[str] = field(default_factory=list)
    target_match: str = "all"


@dataclass(frozen=True)
class Phase:
    id: UUID
    saga_id: UUID
    tracker_id: str
    number: int
    name: str
    status: PhaseStatus
    confidence: float


@dataclass(frozen=True)
class Run:
    id: UUID
    phase_id: UUID
    tracker_id: str
    name: str
    description: str
    acceptance_criteria: list[str]
    declared_files: list[str]
    estimate_hours: float | None
    status: RunStatus
    confidence: float
    session_id: str | None
    branch: str | None
    chronicle_summary: str | None
    pr_url: str | None
    pr_id: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    identifier: str = ""
    url: str = ""
    reviewer_session_id: str | None = None
    review_round: int = 0
    structured_outcome: dict[str, Any] | None = None
    outcome_event_type: str | None = None


@dataclass(frozen=True)
class ConfidenceEvent:
    id: UUID
    run_id: UUID
    event_type: ConfidenceEventType
    delta: float
    score_after: float
    created_at: datetime


@dataclass(frozen=True)
class SessionMessage:
    """A message sent to a running Volundr session (audit record)."""

    id: UUID
    run_id: UUID
    session_id: str
    content: str
    sender: str
    created_at: datetime


@dataclass(frozen=True)
class RavnOutcome:
    """Structured outcome from a ``ravn.task.completed`` event payload.

    Published by the ravn flock coordinator at the end of a task session.
    Fields map directly to the ``produces.schema`` declared by the coordinator
    persona.
    """

    verdict: str
    """Final verdict from the coordinator: ``"approve"`` | ``"retry"`` | ``"escalate"``."""

    tests_passing: bool | None
    """Whether all CI / test suite checks pass. ``None`` means unknown."""

    scope_adherence: float | None
    """Fraction (0.0–1.0) of work that stayed within declared scope. ``None`` means unknown."""

    pr_url: str | None
    """URL of the pull request created by the session, if any."""

    files_changed: list[str]
    """List of file paths changed in the session."""

    summary: str
    """Human-readable one-line summary from the coordinator."""

    authoritative: bool = False
    """Whether the outcome came from a deterministic workflow/runtime stop node."""

    checks: list[dict[str, Any]] = field(default_factory=list)
    """Structured child outcomes that contributed to the final verdict."""


@dataclass(frozen=True)
class DispatcherState:
    id: UUID
    owner_id: str
    running: bool
    threshold: float
    max_concurrent_runs: int
    auto_continue: bool
    updated_at: datetime


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    status: str


@dataclass(frozen=True)
class WorkflowDefinition:
    id: UUID
    name: str
    description: str
    version: str
    scope: WorkflowScope
    owner_id: str | None
    graph: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CampaignStageState:
    stage_id: str
    label: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    reason: str | None = None


@dataclass(frozen=True)
class WorkflowCampaign:
    id: UUID
    slug: str
    name: str
    owner_id: str
    workflow_id: UUID
    workflow_version: str
    workflow_name: str
    workflow_snapshot: dict[str, Any]
    session_id: str
    session_name: str
    status: WorkflowCampaignStatus
    active_stage_id: str | None
    stage_state: list[CampaignStageState]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime | None = None
    completed_at: datetime | None = None
    # Volundr connection the session was launched on. Read paths must resolve
    # this connection, not the owner's primary — a session launched on a
    # non-default cluster is invisible to the default one.
    connection_id: str | None = None


@dataclass(frozen=True)
class PRStatus:
    pr_id: str
    url: str
    state: str
    mergeable: bool
    ci_passed: bool | None


# ---------------------------------------------------------------------------
# Tracker browsing models (read-only, pre-import)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackerProject:
    """A project from an external tracker (read-only browsing model)."""

    id: str
    name: str
    description: str
    status: str
    url: str
    milestone_count: int
    issue_count: int
    slug: str = ""
    progress: float = 0.0
    start_date: str | None = None
    target_date: str | None = None


@dataclass(frozen=True)
class TrackerMilestone:
    """A milestone from an external tracker (read-only browsing model)."""

    id: str
    project_id: str
    name: str
    description: str
    sort_order: int
    progress: float
    target_date: str | None = None


@dataclass(frozen=True)
class TrackerIssue:
    """An issue from an external tracker (read-only browsing model)."""

    id: str
    identifier: str
    title: str
    description: str
    status: str
    status_type: str = ""
    assignee: str | None = None
    labels: list[str] | None = None
    priority: int = 0
    priority_label: str = ""
    estimate: float | None = None
    url: str = ""
    milestone_id: str | None = None


# ---------------------------------------------------------------------------
# Spec structures (LLM decomposition output)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSpec:
    name: str
    description: str
    acceptance_criteria: list[str]
    declared_files: list[str]
    estimate_hours: float
    confidence: float


@dataclass(frozen=True)
class PhaseSpec:
    name: str
    runs: list[RunSpec]


@dataclass(frozen=True)
class PlanRisk:
    kind: str
    message: str


@dataclass(frozen=True)
class SagaStructure:
    name: str
    phases: list[PhaseSpec]
    risks: list[PlanRisk] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Personal access tokens — re-exported from shared niuu module
# ---------------------------------------------------------------------------

from niuu.domain.models import PersonalAccessToken  # noqa: F401, E402

__all__ = ["PersonalAccessToken"]
