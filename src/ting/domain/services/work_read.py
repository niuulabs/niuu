"""Authorized read composition for Ting's work-first user experience.

This module deliberately composes existing records without inventing a new
write-side "Work" aggregate.  Tracker projects, campaigns, and bounded
workflow executions keep their own identities and command surfaces.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from niuu.domain.models import Principal
from ting.domain.models import (
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerProject,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.ticket_coordinator import (
    PENDING_GATES_KEY,
    PULL_REQUEST_KEY,
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_of,
)
from ting.domain.tracker_routing import TrackerRoutingError, select_tracker_for_saga
from ting.domain.workflow_board import (
    BACKLOG_COLUMN_ID,
    DONE_COLUMN_ID,
    GATE_STATUS_PENDING,
    BoardPlacement,
    BoardTicketState,
    BoardWorkflow,
    ProjectBoard,
    WorkflowBoard,
    board_from_snapshot,
    last_board_decision,
    off_board_placement,
    place_on_project,
    place_ticket,
    project_board,
    repository_gap,
    ticket_is_closed,
)
from ting.domain.workflow_execution import ExecutionState, WorkflowExecution
from ting.ports.saga_repository import SagaRepository
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.tracker import TrackerPort, TrackerResolutionFailure
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_execution import WorkflowExecutionRepository

#: A campaign's status as the run status a board places a ticket by.
_CAMPAIGN_RUN_STATUS = {
    WorkflowCampaignStatus.PENDING: RunStatus.RUNNING,
    WorkflowCampaignStatus.RUNNING: RunStatus.RUNNING,
    WorkflowCampaignStatus.BLOCKED: RunStatus.RUNNING,
    WorkflowCampaignStatus.COMPLETED: RunStatus.MERGED,
    WorkflowCampaignStatus.FAILED: RunStatus.FAILED,
}


@dataclass(frozen=True)
class SessionProgress:
    """What a live session says about its run; gates are newest first."""

    gates: list[dict[str, Any]]
    failed: bool = False
    chat_endpoint: str | None = None
    blocked_reason: str | None = None
    #: Why the session cannot answer right now, while it is stopped or starting.
    away_reason: str | None = None


#: Reads one session given its id and the Forge connection it was launched on,
#: which is None when only the session id is known (a dispatched run).
SessionGateReader = Callable[[str, str | None], Awaitable[SessionProgress]]


class WorkKind(StrEnum):
    PROJECT = "project"
    RESEARCH = "research"
    SPECIFICATION = "specification"
    PLANNING = "planning"
    WORKFLOW_LAUNCH = "workflowLaunch"
    WORKFLOW_EXECUTION = "workflowExecution"


class WorkCondition(StrEnum):
    NEEDS_ATTENTION = "needsAttention"
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CoverageError:
    code: str
    message: str


@dataclass(frozen=True)
class SourceCoverage:
    source: str
    connection_id: str | None
    status: CoverageStatus
    error: CoverageError | None = None


@dataclass(frozen=True)
class RawWorkState:
    source: str
    status: str


@dataclass(frozen=True)
class WorkAttention:
    kind: str
    message: str


@dataclass(frozen=True)
class TrackerProjectRef:
    connection_id: str
    provider: str
    project_id: str
    url: str
    status: str


@dataclass(frozen=True)
class WorkflowRef:
    id: str
    version: str
    document_revision: str | None


@dataclass(frozen=True)
class SessionRef:
    id: str
    connection_id: str | None


@dataclass(frozen=True)
class WorkLinks:
    self: str
    specialist: str
    source: str | None = None


@dataclass(frozen=True)
class WorkSummary:
    id: str
    kind: WorkKind
    title: str
    condition: WorkCondition
    raw_state: RawWorkState
    current_step: str | None
    attention: WorkAttention | None
    created_at: datetime | None
    updated_at: datetime | None
    source: TrackerProjectRef | None
    workflow: WorkflowRef | None
    session: SessionRef | None
    links: WorkLinks
    actions: tuple[str, ...] = ()
    campaign_slug: str | None = None


@dataclass(frozen=True)
class TrackerIssueRef:
    connection_id: str
    provider: str
    project_id: str
    issue_id: str
    identifier: str
    url: str
    status: str
    status_type: str


@dataclass(frozen=True)
class OperationalRunRef:
    id: str
    status: str
    session_id: str | None
    reviewer_session_id: str | None
    branch: str | None
    pr_url: str | None
    updated_at: datetime


@dataclass(frozen=True)
class DispatchRef:
    saga_id: str
    issue_id: str
    repo: str
    connection_id: str | None
    workflow_id: str | None


@dataclass(frozen=True)
class TicketCoordinatorRef:
    session_id: str
    status: TicketCoordinatorStatus
    connection_id: str | None
    chat_endpoint: str | None
    start_error: str | None = None


@dataclass(frozen=True)
class TicketWorkflowRun:
    task_id: str
    workflow_name: str
    status: WorkflowCampaignStatus
    session_id: str | None
    current_step: str | None = None
    pr_url: str | None = None


@dataclass(frozen=True)
class WorkTask:
    source: TrackerIssueRef
    title: str
    operational_run: OperationalRunRef | None
    dispatch: DispatchRef | None
    actions: tuple[str, ...] = ()
    board: BoardPlacement | None = None
    coordinator: TicketCoordinatorRef | None = None
    workflow_run: TicketWorkflowRun | None = None
    description: str = ""


@dataclass(frozen=True)
class WorkCollection:
    projects: tuple[WorkSummary, ...]
    campaigns: tuple[WorkSummary, ...]
    executions: tuple[WorkSummary, ...]
    execution_next_cursor: str | None
    coverage: tuple[SourceCoverage, ...]


@dataclass(frozen=True)
class WorkDetail:
    item: WorkSummary
    tasks: tuple[WorkTask, ...] = ()
    coverage: tuple[SourceCoverage, ...] = ()
    board: ProjectBoard | None = None


class WorkNotFoundError(LookupError):
    """The requested authorized source record does not exist."""


class WorkSourceUnavailableError(RuntimeError):
    """A configured source required for a detail read is unavailable."""


@dataclass(frozen=True)
class _TrackerRead:
    adapter: TrackerPort
    projects: tuple[TrackerProject, ...]
    error: bool = False


class WorkReadService:
    """Compose owner-scoped Work views from existing authorized repositories."""

    def __init__(
        self,
        *,
        principal: Principal,
        saga_repo: SagaRepository,
        campaign_repo: WorkflowCampaignRepository,
        trackers: list[TrackerPort],
        execution_repo: WorkflowExecutionRepository | None,
        tracker_resolution_failures: tuple[TrackerResolutionFailure, ...] = (),
        campaign_lifecycle_available: bool = True,
        session_gates: SessionGateReader | None = None,
        coordinator_repo: TicketCoordinatorRepository | None = None,
    ) -> None:
        self._principal = principal
        self._saga_repo = saga_repo
        self._campaign_repo = campaign_repo
        self._trackers = trackers
        self._execution_repo = execution_repo
        self._tracker_resolution_failures = tracker_resolution_failures
        self._campaign_lifecycle_available = campaign_lifecycle_available
        self._session_gates = session_gates
        self._coordinator_repo = coordinator_repo

    async def list_work(
        self,
        *,
        execution_limit: int,
        execution_cursor: str,
    ) -> WorkCollection:
        saga_result, campaign_result, tracker_result = await asyncio.gather(
            self._saga_repo.list_sagas(owner_id=self._principal.user_id),
            self._campaign_repo.list_campaign_summaries(owner_id=self._principal.user_id),
            self._read_tracker_projects(),
            return_exceptions=True,
        )
        for result in (saga_result, campaign_result, tracker_result):
            if isinstance(result, asyncio.CancelledError):
                raise result
        sagas = [] if isinstance(saga_result, BaseException) else saga_result
        campaigns = [] if isinstance(campaign_result, BaseException) else campaign_result
        tracker_reads = () if isinstance(tracker_result, BaseException) else tracker_result
        project_index = _project_index(tracker_reads)
        projects = tuple(
            self._project_summary(saga, project_index=project_index, tracker_reads=tracker_reads)
            for saga in sagas
        )
        campaign_items = tuple(
            _campaign_summary(
                campaign,
                lifecycle_available=self._campaign_lifecycle_available,
            )
            for campaign in campaigns
        )

        executions: tuple[WorkSummary, ...] = ()
        next_cursor: str | None = None
        coverage = list(
            _tracker_coverage(tracker_reads, failures=self._tracker_resolution_failures)
        )
        coverage.extend(
            (
                _repository_coverage("sagas", failed=isinstance(saga_result, BaseException)),
                _repository_coverage(
                    "campaigns", failed=isinstance(campaign_result, BaseException)
                ),
                _repository_coverage(
                    "campaignLifecycle", failed=not self._campaign_lifecycle_available
                ),
            )
        )
        if self._execution_repo is None:
            coverage.append(
                SourceCoverage(
                    source="workflowExecutions",
                    connection_id=None,
                    status=CoverageStatus.UNAVAILABLE,
                    error=CoverageError(
                        code="notConfigured",
                        message="Bounded workflow executions are not configured.",
                    ),
                )
            )
        else:
            try:
                page, raw_next_cursor = await self._execution_repo.list(
                    owner_id=self._principal.user_id,
                    tenant_id=self._principal.tenant_id,
                    limit=execution_limit,
                    cursor=execution_cursor,
                )
            except ValueError:
                raise
            except Exception:
                coverage.append(_repository_coverage("workflowExecutions", failed=True))
            else:
                executions = tuple(_execution_summary(execution) for execution in page)
                next_cursor = raw_next_cursor or None
                coverage.append(
                    SourceCoverage(
                        source="workflowExecutions",
                        connection_id=None,
                        status=(CoverageStatus.PARTIAL if next_cursor else CoverageStatus.COMPLETE),
                        error=(
                            CoverageError(
                                code="morePages",
                                message="More bounded workflow executions are available.",
                            )
                            if next_cursor
                            else None
                        ),
                    )
                )

        return WorkCollection(
            projects=projects,
            campaigns=campaign_items,
            executions=executions,
            execution_next_cursor=next_cursor,
            coverage=tuple(coverage),
        )

    async def get_project(
        self,
        saga_id: UUID,
        *,
        ready_issue_ids: set[str] | None = None,
        dispatch_coverage: SourceCoverage | None = None,
    ) -> WorkDetail:
        saga = await self._saga_repo.get_saga(saga_id, owner_id=self._principal.user_id)
        if saga is None:
            raise WorkNotFoundError(f"Project work not found: {saga_id}")

        try:
            tracker = select_tracker_for_saga(self._trackers, saga)
        except TrackerRoutingError as exc:
            resolution_failure = next(
                (
                    failure
                    for failure in self._tracker_resolution_failures
                    if failure.connection_id == saga.tracker_connection_id
                ),
                None,
            )
            coverage = (
                SourceCoverage(
                    source="tracker",
                    connection_id=saga.tracker_connection_id or None,
                    status=CoverageStatus.UNAVAILABLE,
                    error=CoverageError(
                        code=(
                            resolution_failure.code
                            if resolution_failure is not None
                            else "sourceUnavailable"
                        ),
                        message=(
                            resolution_failure.message
                            if resolution_failure is not None
                            else str(exc)
                        ),
                    ),
                ),
            )
            return WorkDetail(
                item=self._project_summary(saga, project_index={}, tracker_reads=()),
                coverage=coverage + ((dispatch_coverage,) if dispatch_coverage else ()),
            )

        try:
            project, issues = await asyncio.gather(
                tracker.get_project(saga.tracker_id),
                tracker.list_issues(saga.tracker_id),
            )
        except Exception:
            coverage = (
                SourceCoverage(
                    source="tracker",
                    connection_id=tracker.connection_id or None,
                    status=CoverageStatus.UNAVAILABLE,
                    error=CoverageError(
                        code="sourceReadFailed",
                        message="The linked tracker project could not be read.",
                    ),
                ),
            )
            return WorkDetail(
                item=self._project_summary(saga, project_index={}, tracker_reads=()),
                coverage=coverage + ((dispatch_coverage,) if dispatch_coverage else ()),
            )

        coverage = [
            SourceCoverage(
                source="tracker",
                connection_id=tracker.connection_id or None,
                status=CoverageStatus.COMPLETE,
            )
        ]
        operational_runs: list[Run] = []
        operational_failed = False
        try:
            operational_runs.extend(
                await tracker.get_authorized_run_progress_for_saga(
                    saga.tracker_id,
                    owner_id=self._principal.user_id,
                    tenant_id=self._principal.tenant_id,
                )
            )
            if await tracker.has_unscoped_run_progress_for_saga(
                saga.tracker_id,
                owner_id=self._principal.user_id,
                tenant_id=self._principal.tenant_id,
            ):
                operational_failed = True
        except (NotImplementedError, AttributeError):
            operational_failed = True
        except Exception:
            operational_failed = True
        try:
            phases = await self._saga_repo.get_phases_by_saga(saga.id)
            for phase in phases:
                operational_runs.extend(await self._saga_repo.get_runs_by_phase(phase.id))
        except Exception:
            operational_failed = True
        if operational_failed:
            coverage.append(
                SourceCoverage(
                    source="operationalRuns",
                    connection_id=tracker.connection_id or None,
                    status=(
                        CoverageStatus.PARTIAL if operational_runs else CoverageStatus.UNAVAILABLE
                    ),
                    error=CoverageError(
                        code="operationalReadFailed",
                        message="Authorized operational run links could not be read.",
                    ),
                )
            )
        else:
            coverage.append(
                SourceCoverage(
                    source="operationalRuns",
                    connection_id=tracker.connection_id or None,
                    status=CoverageStatus.COMPLETE,
                )
            )
        runs_by_source = _operational_run_index(operational_runs)
        issue_runs = [
            (issue, runs_by_source.get(issue.id) or runs_by_source.get(issue.identifier))
            for issue in issues
        ]
        board = project_board(saga)
        coordinators = await self._ticket_coordinators(saga)
        ticket_history = await self._ticket_runs(saga)
        ticket_runs = {issue_id: runs[-1] for issue_id, runs in ticket_history.items()}
        gates_by_session: dict[str, SessionProgress] = {}
        if board is not None:
            gates_by_session, gate_coverage = await self._read_session_gates(
                _live_sessions(issue_runs, coordinators, ticket_runs)
            )
            coverage.extend(gate_coverage)
        tasks = tuple(
            _task_from_issue(
                saga,
                tracker,
                issue,
                run,
                ready=ready_issue_ids is not None and issue.id in ready_issue_ids,
                placement=(
                    _placement(
                        board,
                        saga,
                        issue,
                        run,
                        ticket_history.get(issue.id, []),
                        coordinators.get(issue.id),
                        gates_by_session,
                    )
                    if board is not None
                    else None
                ),
                coordinator=_coordinator_ref(coordinators.get(issue.id), gates_by_session),
                workflow_run=_workflow_run(ticket_runs.get(issue.id)),
            )
            for issue, run in issue_runs
        )
        if dispatch_coverage is not None:
            coverage.append(dispatch_coverage)
        summary = self._project_summary(
            saga,
            project_index={(tracker.connection_id, saga.tracker_id): project},
            tracker_reads=(_TrackerRead(tracker, (project,)),),
        )
        return WorkDetail(
            item=summary,
            tasks=tasks,
            coverage=tuple(coverage),
            board=board,
        )

    async def tracker_issue(self, saga: Saga, issue_id: str) -> TrackerIssue | None:
        """Return one of a project's tracker issues."""
        try:
            tracker = select_tracker_for_saga(self._trackers, saga)
        except TrackerRoutingError as exc:
            raise WorkSourceUnavailableError(str(exc)) from exc
        issues = await tracker.list_issues(saga.tracker_id)
        return next((issue for issue in issues if issue.id == issue_id), None)

    async def _ticket_coordinators(self, saga: Saga) -> dict[str, TicketCoordinator]:
        if self._coordinator_repo is None:
            return {}
        coordinators = await self._coordinator_repo.list_for_saga(
            saga.id, owner_id=self._principal.user_id
        )
        return {coordinator.issue_id: coordinator for coordinator in coordinators}

    async def _ticket_runs(self, saga: Saga) -> dict[str, list[WorkflowCampaign]]:
        """Return each ticket's workflow runs started through its coordinator, oldest first."""
        campaigns = await self._campaign_repo.list_ticket_campaigns(
            owner_id=self._principal.user_id, saga_id=saga.id
        )
        runs: dict[str, list[WorkflowCampaign]] = {}
        for campaign in campaigns:
            ticket = ticket_of(campaign.metadata)
            if ticket is not None:
                runs.setdefault(ticket[1], []).append(campaign)
        return runs

    async def _read_session_gates(
        self,
        sessions: dict[str, str | None],
    ) -> tuple[dict[str, SessionProgress], tuple[SourceCoverage, ...]]:
        session_ids = list(sessions)
        if not session_ids:
            return {}, (
                SourceCoverage(
                    source="workflowGates", connection_id=None, status=CoverageStatus.COMPLETE
                ),
            )
        if self._session_gates is None:
            return {}, (
                SourceCoverage(
                    source="workflowGates",
                    connection_id=None,
                    status=CoverageStatus.UNAVAILABLE,
                    error=CoverageError(
                        code="notConfigured",
                        message="No Forge connection is available to read workflow gates.",
                    ),
                ),
            )
        results = await asyncio.gather(
            *(self._session_gates(session_id, sessions[session_id]) for session_id in session_ids),
            return_exceptions=True,
        )
        gates: dict[str, SessionProgress] = {}
        for session_id, result in zip(session_ids, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if not isinstance(result, BaseException):
                gates[session_id] = result
        if len(gates) == len(session_ids):
            return gates, (
                SourceCoverage(
                    source="workflowGates",
                    connection_id=None,
                    status=CoverageStatus.COMPLETE,
                ),
            )
        return gates, (
            SourceCoverage(
                source="workflowGates",
                connection_id=None,
                status=CoverageStatus.PARTIAL if gates else CoverageStatus.UNAVAILABLE,
                error=CoverageError(
                    code="sourceReadFailed",
                    message="Workflow gates could not be read for every running ticket.",
                ),
            ),
        )

    async def get_campaign(self, campaign_id: UUID) -> WorkDetail:
        campaign = await self._campaign_repo.get_campaign(campaign_id)
        if campaign is None or campaign.owner_id != self._principal.user_id:
            raise WorkNotFoundError(f"Campaign work not found: {campaign_id}")
        return WorkDetail(
            item=_campaign_summary(
                campaign,
                lifecycle_available=self._campaign_lifecycle_available,
            ),
            coverage=(
                SourceCoverage(
                    source="campaigns",
                    connection_id=None,
                    status=CoverageStatus.COMPLETE,
                ),
                _repository_coverage(
                    "campaignLifecycle", failed=not self._campaign_lifecycle_available
                ),
            ),
        )

    async def get_execution(self, execution_id: UUID) -> WorkDetail:
        if self._execution_repo is None:
            raise WorkSourceUnavailableError("Bounded workflow executions are not configured")
        execution = await self._execution_repo.get(
            execution_id,
            owner_id=self._principal.user_id,
            tenant_id=self._principal.tenant_id,
        )
        if execution is None:
            raise WorkNotFoundError(f"Workflow execution not found: {execution_id}")
        return WorkDetail(
            item=_execution_summary(execution),
            coverage=(
                SourceCoverage(
                    source="workflowExecutions",
                    connection_id=None,
                    status=CoverageStatus.COMPLETE,
                ),
            ),
        )

    async def _read_tracker_projects(self) -> tuple[_TrackerRead, ...]:
        if not self._trackers:
            return ()
        results = await asyncio.gather(
            *(tracker.list_projects() for tracker in self._trackers),
            return_exceptions=True,
        )
        reads: list[_TrackerRead] = []
        for tracker, result in zip(self._trackers, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                reads.append(_TrackerRead(adapter=tracker, projects=(), error=True))
                continue
            reads.append(_TrackerRead(adapter=tracker, projects=tuple(result)))
        return tuple(reads)

    @staticmethod
    def _project_summary(
        saga: Saga,
        *,
        project_index: dict[tuple[str, str], TrackerProject],
        tracker_reads: tuple[_TrackerRead, ...],
    ) -> WorkSummary:
        connection_id, project = _project_for_saga(saga, project_index, tracker_reads)
        provider = saga.tracker_type
        if not provider and connection_id:
            provider = next(
                (
                    read.adapter.provider
                    for read in tracker_reads
                    if read.adapter.connection_id == connection_id
                ),
                "",
            )
        source = TrackerProjectRef(
            connection_id=connection_id,
            provider=provider,
            project_id=saga.tracker_id,
            url=project.url if project else "",
            status=project.status if project else "unknown",
        )
        condition = {
            SagaStatus.ACTIVE: WorkCondition.ACTIVE,
            SagaStatus.COMPLETE: WorkCondition.COMPLETED,
            SagaStatus.FAILED: WorkCondition.FAILED,
        }[saga.status]
        source_missing = project is None and bool(saga.tracker_id)
        if source_missing and saga.status == SagaStatus.ACTIVE:
            condition = WorkCondition.UNKNOWN
        attention = (
            WorkAttention(
                kind="sourceUnavailable",
                message="The linked tracker project is unavailable.",
            )
            if source_missing
            else None
        )
        workflow = _workflow_ref(
            workflow_id=saga.workflow_id,
            workflow_version=saga.workflow_version,
            snapshot=saga.workflow_snapshot,
        )
        actions = ["open"]
        if source.url:
            actions.append("openSource")
        if workflow is not None:
            actions.append("openDefinition")
        return WorkSummary(
            id=f"project:{saga.id}",
            kind=WorkKind.PROJECT,
            title=project.name if project else saga.name,
            condition=condition,
            raw_state=RawWorkState(source="saga", status=saga.status.value.lower()),
            current_step=None,
            attention=attention,
            created_at=saga.created_at,
            updated_at=None,
            source=source,
            workflow=workflow,
            session=None,
            links=WorkLinks(
                self=f"/api/v1/ting/work/project/{saga.id}",
                specialist=f"/ting/sagas/{saga.id}",
                source=source.url or None,
            ),
            actions=tuple(actions),
        )


def _tracker_coverage(
    reads: tuple[_TrackerRead, ...],
    *,
    failures: tuple[TrackerResolutionFailure, ...] = (),
) -> tuple[SourceCoverage, ...]:
    if not reads and not failures:
        return (
            SourceCoverage(
                source="tracker",
                connection_id=None,
                status=CoverageStatus.COMPLETE,
            ),
        )
    resolved = tuple(
        SourceCoverage(
            source="tracker",
            connection_id=read.adapter.connection_id or None,
            status=CoverageStatus.UNAVAILABLE if read.error else CoverageStatus.COMPLETE,
            error=(
                CoverageError(
                    code="sourceReadFailed",
                    message="A tracker source could not be read.",
                )
                if read.error
                else None
            ),
        )
        for read in reads
    )
    unresolved = tuple(
        SourceCoverage(
            source="tracker",
            connection_id=failure.connection_id,
            status=CoverageStatus.UNAVAILABLE,
            error=CoverageError(code=failure.code, message=failure.message),
        )
        for failure in failures
    )
    return resolved + unresolved


def _repository_coverage(source: str, *, failed: bool) -> SourceCoverage:
    return SourceCoverage(
        source=source,
        connection_id=None,
        status=CoverageStatus.UNAVAILABLE if failed else CoverageStatus.COMPLETE,
        error=(
            CoverageError(
                code="sourceReadFailed",
                message=f"The {source} source could not be read.",
            )
            if failed
            else None
        ),
    )


def _project_index(reads: tuple[_TrackerRead, ...]) -> dict[tuple[str, str], TrackerProject]:
    return {
        (read.adapter.connection_id, project.id): project
        for read in reads
        if not read.error
        for project in read.projects
    }


def _project_for_saga(
    saga: Saga,
    index: dict[tuple[str, str], TrackerProject],
    reads: tuple[_TrackerRead, ...],
) -> tuple[str, TrackerProject | None]:
    if saga.tracker_connection_id:
        return (
            saga.tracker_connection_id,
            index.get((saga.tracker_connection_id, saga.tracker_id)),
        )
    matches = [
        (read.adapter.connection_id, project)
        for read in reads
        if not read.error
        for project in read.projects
        if project.id == saga.tracker_id
    ]
    if len(matches) == 1:
        return matches[0]
    return "", None


def _workflow_ref(
    *,
    workflow_id: UUID | None,
    workflow_version: str | None,
    snapshot: dict[str, Any] | None,
) -> WorkflowRef | None:
    if workflow_id is None:
        return None
    data = snapshot if isinstance(snapshot, dict) else {}
    revision = str(data.get("workflow_revision") or data.get("workflow_digest") or "").strip()
    version = str(data.get("version") or workflow_version or "").strip()
    return WorkflowRef(
        id=str(workflow_id),
        version=version,
        document_revision=revision or None,
    )


def _campaign_kind(campaign: WorkflowCampaign) -> WorkKind:
    surface = str(campaign.metadata.get("surface") or "").strip()
    snapshot = campaign.workflow_snapshot if isinstance(campaign.workflow_snapshot, dict) else {}
    graph = snapshot.get("graph") if isinstance(snapshot.get("graph"), dict) else {}
    tags = {str(tag).strip().lower() for tag in (graph.get("tags") or []) if str(tag).strip()}
    if surface == "ting.plan":
        return WorkKind.PLANNING
    if surface == "ting.specs" or tags & {"spec", "specification"}:
        return WorkKind.SPECIFICATION
    if surface == "ting.research" or "research" in tags:
        return WorkKind.RESEARCH
    return WorkKind.WORKFLOW_LAUNCH


def _campaign_summary(
    campaign: WorkflowCampaign,
    *,
    lifecycle_available: bool,
) -> WorkSummary:
    kind = _campaign_kind(campaign)
    explicit_attention = bool(
        campaign.metadata.get("pending_workflow_gates")
        or campaign.metadata.get("latest_help_needed")
    )
    condition = {
        WorkflowCampaignStatus.PENDING: WorkCondition.ACTIVE,
        WorkflowCampaignStatus.RUNNING: WorkCondition.ACTIVE,
        WorkflowCampaignStatus.BLOCKED: (
            WorkCondition.NEEDS_ATTENTION if explicit_attention else WorkCondition.WAITING
        ),
        WorkflowCampaignStatus.COMPLETED: WorkCondition.COMPLETED,
        WorkflowCampaignStatus.FAILED: WorkCondition.FAILED,
    }[campaign.status]
    attention = None
    if explicit_attention:
        attention = WorkAttention(kind="decision", message="This work is waiting for input.")
    elif campaign.status == WorkflowCampaignStatus.FAILED:
        attention = WorkAttention(kind="failed", message="This workflow run failed.")
    if not lifecycle_available and campaign.status in {
        WorkflowCampaignStatus.PENDING,
        WorkflowCampaignStatus.RUNNING,
        WorkflowCampaignStatus.BLOCKED,
    }:
        condition = WorkCondition.UNKNOWN
        attention = WorkAttention(
            kind="sourceUnavailable",
            message="Live workflow campaign status is unavailable.",
        )
    active_stage = next(
        (stage for stage in campaign.stage_state if stage.stage_id == campaign.active_stage_id),
        None,
    )
    revision = str(
        campaign.workflow_snapshot.get("workflow_revision")
        or campaign.workflow_snapshot.get("workflow_digest")
        or ""
    ).strip()
    workflow = WorkflowRef(
        id=str(campaign.workflow_id),
        version=campaign.workflow_version,
        document_revision=revision or None,
    )
    specialist = {
        WorkKind.RESEARCH: f"/ting/research/{campaign.slug}",
        WorkKind.SPECIFICATION: f"/ting/specs/{campaign.slug}",
        WorkKind.PLANNING: f"/ting/plan/{campaign.slug}",
        WorkKind.WORKFLOW_LAUNCH: "/ting/workflows",
    }[kind]
    actions = ["open", "openDefinition"]
    if campaign.session_id:
        actions.append("openSession")
    if kind == WorkKind.SPECIFICATION and explicit_attention:
        actions.append("review")
    return WorkSummary(
        id=f"campaign:{campaign.id}",
        kind=kind,
        title=campaign.name,
        condition=condition,
        raw_state=RawWorkState(source="campaign", status=campaign.status.value),
        current_step=active_stage.label if active_stage else None,
        attention=attention,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        source=None,
        workflow=workflow,
        session=(
            SessionRef(id=campaign.session_id, connection_id=campaign.connection_id)
            if campaign.session_id
            else None
        ),
        links=WorkLinks(
            self=f"/api/v1/ting/work/campaign/{campaign.id}",
            specialist=specialist,
        ),
        actions=tuple(actions),
        campaign_slug=campaign.slug,
    )


def _execution_summary(execution: WorkflowExecution) -> WorkSummary:
    condition = {
        ExecutionState.PENDING: WorkCondition.ACTIVE,
        ExecutionState.RUNNING: WorkCondition.ACTIVE,
        ExecutionState.WAITING: WorkCondition.WAITING,
        ExecutionState.BLOCKED: WorkCondition.NEEDS_ATTENTION,
        ExecutionState.CANCELING: WorkCondition.WAITING,
        ExecutionState.CANCELED: WorkCondition.FAILED,
        ExecutionState.COMPLETED: WorkCondition.COMPLETED,
        ExecutionState.FAILED: WorkCondition.FAILED,
    }[execution.state]
    attention = None
    if execution.state == ExecutionState.BLOCKED:
        attention = WorkAttention(
            kind="blocked",
            message=execution.suspension_reason or "A child workflow is blocked.",
        )
    elif execution.state == ExecutionState.FAILED:
        attention = WorkAttention(kind="failed", message="This workflow execution failed.")
    snapshot_version = str(execution.workflow_snapshot.get("version") or "").strip()
    actions = ["open", "openDefinition"]
    if execution.parent_session_id:
        actions.append("openSession")
    return WorkSummary(
        id=f"execution:{execution.id}",
        kind=WorkKind.WORKFLOW_EXECUTION,
        title=execution.name,
        condition=condition,
        raw_state=RawWorkState(source="workflowExecution", status=execution.state.value),
        current_step=execution.suspension_reason or None,
        attention=attention,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
        source=None,
        workflow=WorkflowRef(
            id=str(execution.workflow_id),
            version=snapshot_version,
            document_revision=execution.workflow_digest,
        ),
        session=(
            SessionRef(
                id=execution.parent_session_id,
                connection_id=execution.connection_id or None,
            )
            if execution.parent_session_id
            else None
        ),
        links=WorkLinks(
            self=f"/api/v1/ting/work/execution/{execution.id}",
            specialist=f"/ting/workflows/runs?execution={execution.id}",
        ),
        actions=tuple(actions),
    )


def _operational_run_index(runs: list[Run]) -> dict[str, Run]:
    index: dict[str, Run] = {}
    for run in sorted(runs, key=lambda item: item.updated_at):
        if run.tracker_id:
            index[run.tracker_id] = run
        if run.identifier:
            index[run.identifier] = run
    return index


def _live_sessions(
    issue_runs: list[tuple[TrackerIssue, Run | None]],
    coordinators: dict[str, TicketCoordinator],
    ticket_runs: dict[str, WorkflowCampaign],
) -> dict[str, str | None]:
    """Sessions whose state decides where a ticket sits, each with its Forge connection.

    A connection is None when only the session id is known. Where one session
    is named both ways, the known connection wins so it is read from one Forge.
    """
    sessions: dict[str, str | None] = {}

    def add(session_id: str, connection_id: str | None) -> None:
        sessions[session_id] = connection_id or sessions.get(session_id)

    for _, run in issue_runs:
        if run is not None and run.session_id and run.status != RunStatus.MERGED:
            add(run.session_id, None)
    for coordinator in coordinators.values():
        if coordinator.status == TicketCoordinatorStatus.ACTIVE:
            add(coordinator.session_id, coordinator.connection_id)
    for campaign in ticket_runs.values():
        if campaign.session_id and campaign.status != WorkflowCampaignStatus.COMPLETED:
            add(campaign.session_id, campaign.connection_id)
    return dict(sorted(sessions.items()))


def _coordinator_ref(
    coordinator: TicketCoordinator | None,
    sessions: dict[str, SessionProgress],
) -> TicketCoordinatorRef | None:
    if coordinator is None:
        return None
    progress = sessions.get(coordinator.session_id)
    return TicketCoordinatorRef(
        session_id=coordinator.session_id,
        status=coordinator.status,
        connection_id=coordinator.connection_id,
        chat_endpoint=progress.chat_endpoint if progress is not None else None,
        start_error=coordinator.start_error,
    )


def _workflow_run(campaign: WorkflowCampaign | None) -> TicketWorkflowRun | None:
    if campaign is None:
        return None
    return TicketWorkflowRun(
        task_id=campaign.slug,
        workflow_name=campaign.workflow_name,
        status=campaign.status,
        session_id=campaign.session_id or None,
        current_step=next(
            (
                stage.label
                for stage in campaign.stage_state
                if stage.stage_id == campaign.active_stage_id
            ),
            None,
        ),
        pr_url=_pull_request_url(campaign),
    )


def _pull_request_url(campaign: WorkflowCampaign) -> str | None:
    pull_request = campaign.metadata.get(PULL_REQUEST_KEY)
    return str(pull_request["url"]) if isinstance(pull_request, dict) else None


def _placement(
    project: ProjectBoard,
    saga: Saga,
    issue: TrackerIssue,
    run: Run | None,
    campaigns: list[WorkflowCampaign],
    coordinator: TicketCoordinator | None,
    sessions: dict[str, SessionProgress],
) -> BoardPlacement:
    """Place a ticket by its newest run, read through that run's own pinned workflow."""
    placement = _ticket_placement(project, saga, issue, run, campaigns, coordinator, sessions)
    session_id = campaigns[-1].session_id if campaigns else run.session_id if run else None
    progress = sessions.get(session_id or "")
    return replace(
        placement,
        passed_workflow_ids=_passed_workflows(saga, run, campaigns),
        last_decision=last_board_decision(progress.gates) if progress is not None else None,
    )


def _ticket_placement(
    project: ProjectBoard,
    saga: Saga,
    issue: TrackerIssue,
    run: Run | None,
    campaigns: list[WorkflowCampaign],
    coordinator: TicketCoordinator | None,
    sessions: dict[str, SessionProgress],
) -> BoardPlacement:
    if ticket_is_closed(issue.status_type):
        return BoardPlacement(column_id=DONE_COLUMN_ID, state=BoardTicketState.DONE)
    campaign = campaigns[-1] if campaigns else None
    if run is not None and campaign is not None and run.created_at > campaign.created_at:
        campaign = None
    requested = _requested_workflow(project, coordinator, run, campaign)
    if requested is not None:
        return _placement_starting(project, issue, requested, coordinator, sessions)
    if campaign is not None:
        return _placement_of_run(
            project,
            issue,
            workflow_id=str(campaign.workflow_id),
            pinned=board_from_snapshot(campaign.workflow_snapshot),
            run_status=_CAMPAIGN_RUN_STATUS[campaign.status],
            session_id=campaign.session_id,
            sessions=sessions,
            remembered_gates=_remembered_gates(campaign),
            failure_reason=str(campaign.metadata.get("failure_error") or "") or None,
        )
    if run is not None:
        # A dispatched run has no pin of its own: it ran the saga's assigned workflow.
        return _placement_of_run(
            project,
            issue,
            workflow_id=str(saga.workflow_id),
            pinned=board_from_snapshot(saga.workflow_snapshot),
            run_status=run.status,
            session_id=run.session_id,
            sessions=sessions,
        )
    if coordinator is not None and coordinator.status == TicketCoordinatorStatus.ACTIVE:
        return _placement_starting(project, issue, project.first_workflow, coordinator, sessions)
    return BoardPlacement(column_id=BACKLOG_COLUMN_ID, state=BoardTicketState.NOT_STARTED)


def _requested_workflow(
    project: ProjectBoard,
    coordinator: TicketCoordinator | None,
    run: Run | None,
    campaign: WorkflowCampaign | None,
) -> BoardWorkflow | None:
    """The workflow a coordinator was asked to start, until a newer run appears."""
    if coordinator is None or coordinator.status != TicketCoordinatorStatus.ACTIVE:
        return None
    if coordinator.requested_workflow_id is None or coordinator.requested_at is None:
        return None
    newest = campaign or run
    if newest is not None and newest.created_at >= coordinator.requested_at:
        return None
    return project.workflow(coordinator.requested_workflow_id)


def _placement_of_run(
    project: ProjectBoard,
    issue: TrackerIssue,
    *,
    workflow_id: str,
    pinned: WorkflowBoard | None,
    run_status: RunStatus,
    session_id: str | None,
    sessions: dict[str, SessionProgress],
    remembered_gates: tuple[dict[str, Any], ...] = (),
    failure_reason: str | None = None,
) -> BoardPlacement:
    live = run_status != RunStatus.MERGED and bool(session_id)
    if live and session_id not in sessions:
        return BoardPlacement(column_id=None, state=BoardTicketState.UNKNOWN)
    if pinned is None:
        return off_board_placement()
    progress = sessions.get(session_id or "", SessionProgress(gates=[]))
    away = progress.away_reason is not None
    placement = place_on_project(
        project,
        workflow_id,
        place_ticket(
            pinned,
            run_status=run_status,
            issue_status_type=issue.status_type,
            gates=list(remembered_gates) if away else progress.gates,
            session_failed=progress.failed,
            blocked_reason=progress.blocked_reason or failure_reason,
        ),
    )
    if (
        away
        and remembered_gates
        and placement.state in {BoardTicketState.NEEDS_DECISION, BoardTicketState.BLOCKED}
    ):
        # Where the ticket is, without a decision the session could not take yet.
        return replace(
            placement,
            state=BoardTicketState.BLOCKED,
            gate=None,
            reason=progress.away_reason,
        )
    return placement


def _remembered_gates(campaign: WorkflowCampaign) -> tuple[dict[str, Any], ...]:
    """The gate the run was last waiting on, kept for when its session cannot be asked."""
    gates = campaign.metadata.get(PENDING_GATES_KEY)
    if not isinstance(gates, list):
        return ()
    return tuple(
        {**gate, "status": GATE_STATUS_PENDING} for gate in gates if isinstance(gate, dict)
    )


def _placement_starting(
    project: ProjectBoard,
    issue: TrackerIssue,
    workflow: BoardWorkflow,
    coordinator: TicketCoordinator,
    sessions: dict[str, SessionProgress],
) -> BoardPlacement:
    """The coordinator was asked to start this workflow and its run has not appeared."""
    if coordinator.start_error:
        return BoardPlacement(
            column_id=workflow.board.first_column.id,
            state=BoardTicketState.BLOCKED,
            reason=coordinator.start_error,
        )
    progress = sessions.get(coordinator.session_id)
    if progress is None:
        return BoardPlacement(column_id=None, state=BoardTicketState.UNKNOWN)
    if progress.failed:
        return place_on_project(
            project,
            workflow.id,
            place_ticket(
                workflow.board,
                run_status=RunStatus.RUNNING,
                issue_status_type=issue.status_type,
                gates=[],
                session_failed=True,
                blocked_reason=progress.blocked_reason,
            ),
        )
    return BoardPlacement(column_id=workflow.board.first_column.id, state=BoardTicketState.STARTING)


def _passed_workflows(
    saga: Saga,
    run: Run | None,
    campaigns: list[WorkflowCampaign],
) -> tuple[str, ...]:
    passed = [
        str(campaign.workflow_id)
        for campaign in campaigns
        if campaign.status == WorkflowCampaignStatus.COMPLETED
    ]
    if run is not None and run.status == RunStatus.MERGED and saga.workflow_id is not None:
        passed.append(str(saga.workflow_id))
    return tuple(dict.fromkeys(passed))


def _task_from_issue(
    saga: Saga,
    tracker: TrackerPort,
    issue: TrackerIssue,
    run: Run | None,
    *,
    ready: bool,
    placement: BoardPlacement | None = None,
    coordinator: TicketCoordinatorRef | None = None,
    workflow_run: TicketWorkflowRun | None = None,
) -> WorkTask:
    operational = (
        OperationalRunRef(
            id=str(run.id),
            status=run.status.value.lower(),
            session_id=run.session_id,
            reviewer_session_id=run.reviewer_session_id,
            branch=run.branch,
            pr_url=run.pr_url,
            updated_at=run.updated_at,
        )
        if run is not None
        else None
    )
    dispatch = None
    actions = ["openSource"] if issue.url else []
    if run is not None and run.session_id:
        actions.append("openSession")
    if run is not None and run.status.value in {"REVIEW", "ESCALATED"}:
        actions.append("review")
    has_workflow = saga.workflow_id is not None or bool(saga.board_workflows)
    if ready and (saga.repos or (has_workflow and repository_gap(saga) is None)):
        dispatch = DispatchRef(
            saga_id=str(saga.id),
            issue_id=issue.id,
            repo=saga.repos[0] if saga.repos else "",
            connection_id=saga.instance_id,
            workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
        )
        actions.append("dispatch")
    return WorkTask(
        source=TrackerIssueRef(
            connection_id=tracker.connection_id,
            provider=tracker.provider or saga.tracker_type,
            project_id=saga.tracker_id,
            issue_id=issue.id,
            identifier=issue.identifier,
            url=issue.url,
            status=issue.status,
            status_type=issue.status_type,
        ),
        title=issue.title,
        description=issue.description,
        operational_run=operational,
        dispatch=dispatch,
        actions=tuple(actions),
        board=placement,
        coordinator=coordinator,
        workflow_run=workflow_run,
    )
