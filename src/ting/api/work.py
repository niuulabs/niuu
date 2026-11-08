"""Work projection over Ting's existing bounded contexts.

Everything here reads, apart from two writes: a gate decision, which the step engine
records against the run's open gate, and starting a ticket's workflow, which creates
the run. A run has no single session, so its gates and reviews are read from the
step ledger, never from a session.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from identity.ports import AuthorizationDeniedError, AuthorizationEvaluationError
from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.sagas import resolve_saga_repo
from ting.api.tracker import resolve_trackers
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import Saga, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.dispatch_service import DispatchService
from ting.domain.services.review_list import ReviewKind, ReviewState, review_list, review_run
from ting.domain.services.ticket_coordinator import (
    TicketCoordinatorService,
    TicketWorkflow,
)
from ting.domain.services.ticket_review import RunReview, ledger_reviews
from ting.domain.services.work_read import (
    CoverageError,
    CoverageStatus,
    RunGateReader,
    SessionGateReader,
    SessionProgress,
    SourceCoverage,
    WorkCondition,
    WorkDetail,
    WorkKind,
    WorkNotFoundError,
    WorkReadService,
    WorkSourceUnavailableError,
    WorkSummary,
    WorkTask,
)
from ting.domain.services.workflow_runs import WorkflowRuns
from ting.domain.ticket_coordinator import TicketCoordinatorStatus, ticket_of
from ting.domain.workflow_board import (
    BACKLOG_COLUMN_ID,
    GATE_STATUS_PENDING,
    SESSION_GONE_REASON,
    BoardColumnKind,
    BoardTicketState,
    BoardWorkflow,
    ProjectBoard,
    repository_gap,
)
from ting.domain.workflow_steps import (
    ActorKind,
    GateActor,
    GateDecision,
    GateRefusedError,
    RunStateError,
)
from ting.ports.saga_repository import SagaRepository
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.tracker import TrackerPort, TrackerResolution
from ting.ports.volundr import VolundrFactory, VolundrPort, VolundrSession
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_execution import WorkflowExecutionRepository

_SESSION_ERROR_STATE = "error"
_CLOSED_TICKET_STATUS_TYPES = frozenset({"completed", "canceled"})
# Starting is here because no run exists yet: a start that never produced one can be asked again.
_STARTABLE_STATES = frozenset(
    {BoardTicketState.NOT_STARTED, BoardTicketState.READY, BoardTicketState.STARTING}
)

logger = logging.getLogger(__name__)


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class _ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=_camel, populate_by_name=True)


class CoverageErrorResponse(_ResponseModel):
    code: str
    message: str


class SourceCoverageResponse(_ResponseModel):
    source: str
    connection_id: str | None
    status: CoverageStatus
    error: CoverageErrorResponse | None = None


class RawWorkStateResponse(_ResponseModel):
    source: str
    status: str


class WorkAttentionResponse(_ResponseModel):
    kind: Literal["decision", "blocked", "failed", "sourceUnavailable"]
    message: str


class TrackerProjectRefResponse(_ResponseModel):
    connection_id: str
    provider: str
    project_id: str
    url: str
    status: str


class WorkflowRefResponse(_ResponseModel):
    id: str
    version: str
    document_revision: str | None


class SessionRefResponse(_ResponseModel):
    id: str
    connection_id: str | None


class WorkLinksResponse(_ResponseModel):
    self: str
    specialist: str
    source: str | None = None


class WorkSummaryResponse(_ResponseModel):
    id: str
    kind: WorkKind
    title: str
    condition: WorkCondition
    raw_state: RawWorkStateResponse
    current_step: str | None
    attention: WorkAttentionResponse | None
    created_at: datetime | None
    updated_at: datetime | None
    source: TrackerProjectRefResponse | None
    workflow: WorkflowRefResponse | None
    session: SessionRefResponse | None
    links: WorkLinksResponse
    actions: list[str]
    campaign_slug: str | None = None


class TrackerIssueRefResponse(_ResponseModel):
    connection_id: str
    provider: str
    project_id: str
    issue_id: str
    identifier: str
    url: str
    status: str
    status_type: str


class OperationalRunRefResponse(_ResponseModel):
    id: str
    status: str
    session_id: str | None
    reviewer_session_id: str | None
    branch: str | None
    pr_url: str | None
    updated_at: datetime


class DispatchRefResponse(_ResponseModel):
    saga_id: str
    issue_id: str
    repo: str
    connection_id: str | None
    workflow_id: str | None


class BoardGateResponse(_ResponseModel):
    id: str
    activation_id: str
    node_id: str
    label: str
    condition: str
    instructions: str
    summary: str
    requested_at: str
    attempt: int


class BoardDecisionResponse(_ResponseModel):
    label: str
    status: str
    decided_by: str
    notes: str
    decided_at: str


class BoardPlacementResponse(_ResponseModel):
    column_id: str | None
    state: BoardTicketState
    gate: BoardGateResponse | None = None
    reason: str | None = None
    passed_workflow_ids: list[str] = []
    last_decision: BoardDecisionResponse | None = None


class TicketCoordinatorRefResponse(_ResponseModel):
    session_id: str
    status: TicketCoordinatorStatus
    connection_id: str | None
    chat_endpoint: str | None
    start_error: str | None = None


class TicketWorkflowRunResponse(_ResponseModel):
    task_id: str
    workflow_name: str
    status: WorkflowCampaignStatus
    session_id: str | None
    current_step: str | None = None
    pr_url: str | None = None


class WorkTaskResponse(_ResponseModel):
    source: TrackerIssueRefResponse
    title: str
    description: str = ""
    operational_run: OperationalRunRefResponse | None
    dispatch: DispatchRefResponse | None
    actions: list[str]
    board: BoardPlacementResponse | None = None
    coordinator: TicketCoordinatorRefResponse | None = None
    workflow_run: TicketWorkflowRunResponse | None = None


class ReviewSourceResponse(_ResponseModel):
    kind: str
    value: str


class ReviewPanelResponse(_ResponseModel):
    widget: str
    title: str
    source: ReviewSourceResponse | None = None
    files: list[str] = []


class BoardColumnResponse(_ResponseModel):
    id: str
    title: str
    kind: BoardColumnKind
    review: str | None = None
    panels: list[ReviewPanelResponse] = []
    approve_column_id: str | None = None
    changes_column_id: str | None = None
    workflow_id: str | None = None
    workflow_name: str | None = None


class WorkBoardResponse(_ResponseModel):
    columns: list[BoardColumnResponse]


class WorkCollectionResponse(_ResponseModel):
    projects: list[WorkSummaryResponse]
    campaigns: list[WorkSummaryResponse]
    executions: list[WorkSummaryResponse]
    execution_next_cursor: str | None
    coverage: list[SourceCoverageResponse]


class WorkDetailResponse(_ResponseModel):
    item: WorkSummaryResponse
    tasks: list[WorkTaskResponse]
    coverage: list[SourceCoverageResponse]
    board: WorkBoardResponse | None = None


class GateDecisionRequest(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)

    gate_id: str
    activation_id: str
    decision: Literal["approve", "request_changes"]
    notes: str = ""


class GateDecisionResponse(_ResponseModel):
    status: Literal["resolved"]
    session_id: str
    gate_id: str


class TicketStartRequest(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)

    column_id: str | None = None


class TicketStartResponse(_ResponseModel):
    status: Literal["coordinating"]
    session_id: str


class ReviewArtifactResponse(_ResponseModel):
    """A document version the gate shows: Forge's publication by key and revision."""

    artifact_key: str
    provider_key: str
    revision_id: str
    name: str
    version_label: str | None
    mime: str | None
    url: str | None


class ReviewRevisionResponse(_ResponseModel):
    number: int
    gate_id: str
    activation_id: str
    requested_at: str
    status: str
    summary: str
    decided_by: str
    notes: str
    decided_at: str
    fields: dict[str, str]
    artifacts: list[ReviewArtifactResponse] = []


class RunReviewResponse(_ResponseModel):
    column_id: str
    title: str
    review: str | None
    workflow_name: str
    task_id: str
    session_id: str
    panels: list[ReviewPanelResponse]
    revisions: list[ReviewRevisionResponse]
    chat_endpoint: str | None = None


class TicketReviewsResponse(_ResponseModel):
    reviews: list[RunReviewResponse]
    unreadable_runs: list[str]
    plan_slug: str | None = None


class ReviewTicketResponse(_ResponseModel):
    project_id: str
    issue_id: str


class ReviewSessionResponse(_ResponseModel):
    session_id: str
    member: str
    connection_id: str | None = None


class ReviewRunResponse(_ResponseModel):
    slug: str
    title: str
    workflow_name: str
    prompt: str
    session_id: str
    repo: str
    branch: str
    started_by: str | None = None
    #: The ticket the run was started for; its review page names it and talks to its coordinator.
    ticket: ReviewTicketResponse | None = None
    #: Every session the run worked in: one per member of a step-engine run.
    sessions: list[ReviewSessionResponse] = []


class RunReviewsResponse(_ResponseModel):
    run: ReviewRunResponse
    reviews: list[RunReviewResponse]
    unreadable_runs: list[str]


class ReviewItemResponse(_ResponseModel):
    task_id: str
    kind: ReviewKind
    title: str
    workflow_name: str
    state: ReviewState
    gate_label: str
    updated_at: datetime
    project_id: str | None = None
    project_name: str | None = None
    issue_id: str | None = None
    identifier: str | None = None
    started_by: str | None = None


class ReviewListResponse(_ResponseModel):
    items: list[ReviewItemResponse]


class ReviewWidgetResponse(_ResponseModel):
    module: str
    name: str
    title: str
    html: str


async def resolve_work_execution_repo(
    request: Request,
) -> WorkflowExecutionRepository | None:
    return getattr(request.app.state, "work_execution_read_repo", None)


async def resolve_work_trackers(
    trackers: list[TrackerPort] = Depends(resolve_trackers),
) -> TrackerResolution:
    """Resolve tracker sources, retaining the legacy resolver as a fallback."""
    return TrackerResolution(adapters=tuple(trackers))


def _service(
    *,
    principal: Principal,
    saga_repo: SagaRepository,
    campaign_repo: WorkflowCampaignRepository,
    tracker_resolution: TrackerResolution,
    execution_repo: WorkflowExecutionRepository | None,
    campaign_lifecycle_available: bool,
    session_gates: SessionGateReader | None = None,
    run_gates: RunGateReader | None = None,
    coordinator_repo: TicketCoordinatorRepository | None = None,
) -> WorkReadService:
    return WorkReadService(
        principal=principal,
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=list(tracker_resolution.adapters),
        execution_repo=execution_repo,
        tracker_resolution_failures=tracker_resolution.failures,
        campaign_lifecycle_available=campaign_lifecycle_available,
        session_gates=session_gates,
        run_gates=run_gates,
        coordinator_repo=coordinator_repo,
    )


def _workflow_runs(request: Request) -> WorkflowRuns:
    runs = getattr(request.app.state, "workflow_runs", None)
    if runs is None:
        raise HTTPException(status_code=503, detail="The workflow step engine is not configured")
    return runs


def _run_gate_reader(request: Request) -> RunGateReader | None:
    """Reads a run's gates from its step ledger; ``None`` where the engine is not configured."""
    runs = getattr(request.app.state, "workflow_runs", None)
    if runs is None:
        return None

    async def read(campaign: WorkflowCampaign) -> SessionProgress:
        return SessionProgress(gates=await runs.gates(campaign))

    return read


def _require_changes_note(body: GateDecisionRequest) -> None:
    if body.decision == "request_changes" and not body.notes.strip():
        raise HTTPException(status_code=422, detail="Say what should change")


def _is_gate_shown(gate: Mapping[str, Any], body: GateDecisionRequest) -> bool:
    """Whether a session's gate is still the pending one the reviewer decided on."""
    return (
        gate.get("status") == GATE_STATUS_PENDING
        and gate.get("id") == body.gate_id
        and gate.get("activation_id") == body.activation_id
    )


async def _owned_run(
    campaign_repo: WorkflowCampaignRepository, slug: str, principal: Principal
) -> WorkflowCampaign:
    campaign = await campaign_repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {slug}")
    return campaign


async def _read_reviews(runs: list[WorkflowCampaign], request: Request) -> list[RunReview]:
    """Each run's reviews, read from its step ledger."""
    workflow_runs = _workflow_runs(request)
    reviews: list[RunReview] = []
    for campaign in runs:
        reviews.extend(ledger_reviews(campaign, await workflow_runs.state(campaign.id)))
    return reviews


async def _decide_shown_gate(
    request: Request,
    campaign: WorkflowCampaign,
    body: GateDecisionRequest,
    principal: Principal,
) -> GateDecisionResponse:
    """Record a person's decision on the gate they saw, if it is still the one waiting."""
    runs = _workflow_runs(request)
    shown = next(
        (gate for gate in await runs.pending_gates(campaign) if _is_gate_shown(gate, body)),
        None,
    )
    if shown is None:
        raise HTTPException(
            status_code=409,
            detail="This gate changed after you opened it. Review it again.",
        )
    try:
        await runs.resolve_gate(
            campaign,
            str(shown["node_id"]),
            GateDecision(body.decision),
            by=GateActor(ActorKind.PERSON, principal.user_id, principal.email),
            note=body.notes.strip(),
        )
    except (GateRefusedError, RunStateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return GateDecisionResponse(status="resolved", session_id="", gate_id=body.gate_id)


def _refuse_active_run(task: WorkTask) -> None:
    """Runs move their own ticket, so a new start waits until none is active."""
    placement = task.board
    identifier = task.source.identifier
    if placement is None or placement.state in _STARTABLE_STATES:
        return
    if placement.state == BoardTicketState.DONE:
        raise HTTPException(status_code=409, detail=f"{identifier} is done")
    if placement.state == BoardTicketState.UNKNOWN:
        raise HTTPException(
            status_code=409, detail=f"Ting cannot read where {identifier} is right now"
        )
    if task.coordinator is not None and task.coordinator.start_error:
        return
    run = task.workflow_run
    if run is not None and run.status == WorkflowCampaignStatus.FAILED:
        return
    raise HTTPException(status_code=409, detail=f"{identifier} already has a run in progress")


def _workflow_to_start(board: ProjectBoard, task: WorkTask, column_id: str | None) -> BoardWorkflow:
    """The workflow a ticket enters: only ever at its first column."""
    placement = task.board
    target = column_id or (placement.column_id if placement else None)
    if target == BACKLOG_COLUMN_ID and column_id is None:
        return board.first_workflow
    workflow = board.workflow_starting_at(target or "")
    if workflow is not None:
        return workflow
    column = next((item for item in board.columns if item.id == target), None)
    if column is None or column.workflow_id is None:
        raise HTTPException(status_code=409, detail="Choose a column that starts a workflow")
    owner = board.workflow(column.workflow_id)
    raise HTTPException(
        status_code=409,
        detail=f"{owner.name} starts at {owner.board.first_column.title}",
    )


def _coordinator_repo(request: Request) -> TicketCoordinatorRepository | None:
    return getattr(request.app.state, "ticket_coordinator_repo", None)


def _coordinator_service(
    request: Request,
    *,
    campaign_repo: WorkflowCampaignRepository,
    principal: Principal,
) -> TicketCoordinatorService:
    repo = _coordinator_repo(request)
    if repo is None:
        raise HTTPException(status_code=503, detail="Ticket coordinators are not configured")
    return TicketCoordinatorService(
        repo=repo,
        campaigns=campaign_repo,
        runs=_workflow_runs(request),
        principal=principal,
    )


async def _close_finished_coordinators(
    detail: WorkDetail,
    *,
    saga_id: UUID,
    request: Request,
) -> WorkDetail:
    """A ticket's record lives as long as its ticket, so a closed ticket ends it."""
    finished = [
        task
        for task in detail.tasks
        if task.coordinator is not None
        and task.coordinator.status == TicketCoordinatorStatus.ACTIVE
        and task.source.status_type.strip().lower() in _CLOSED_TICKET_STATUS_TYPES
    ]
    if not finished:
        return detail
    repo = _coordinator_repo(request)
    for task in finished:
        await repo.close(saga_id, task.source.issue_id)
    return detail


async def _forge_adapter(request: Request, principal: Principal) -> VolundrPort | None:
    factory = getattr(request.app.state, "volundr_factory", None)
    if factory is None:
        return None
    return await factory.primary_for_principal(principal)


async def _project_forge(request: Request, principal: Principal, saga: Saga) -> VolundrPort | None:
    """The Forge a project's tickets run on: the one it names, else the user's primary."""
    if not saga.instance_id:
        return await _forge_adapter(request, principal)
    factory = getattr(request.app.state, "volundr_factory", None)
    if factory is None:
        return None
    return await factory.for_connection(principal.user_id, saga.instance_id)


class ForgeSessionUnreadableError(RuntimeError):
    """A session's Forge could not be asked, so whether the session exists is not known."""


class _ForgeSessions:
    """Reads sessions from the Forge each one lives on.

    A board spans sessions on several Forges, because unpinned launches are
    placed across every registered one. A session is read from the Forge it
    was launched on when that is known; otherwise every Forge of the principal
    is asked. The Forges are resolved once per request, never per ticket, and a
    session counts as gone only when every Forge that was asked answered
    without it.
    """

    def __init__(self, factory: VolundrFactory, *, principal: Principal, auth_token: str | None):
        self._factory = factory
        self._principal = principal
        self._auth_token = auth_token
        self._forges: asyncio.Task[tuple[list[VolundrPort], int]] | None = None

    async def read(self, session_id: str, connection_id: str | None) -> SessionProgress:
        if connection_id:
            adapter = await self._forge_for(connection_id)
            session = await self._get_session(adapter, session_id)
        else:
            adapter, session = await self._locate(session_id)
        if adapter is None or session is None:
            # Every Forge asked answered, and none has it: known, not unreadable.
            return SessionProgress(gates=[], failed=True, blocked_reason=SESSION_GONE_REASON)
        return SessionProgress(
            gates=[],
            failed=session.activity_state == _SESSION_ERROR_STATE,
            chat_endpoint=session.chat_endpoint,
        )

    async def _forges_of_principal(self) -> tuple[list[VolundrPort], int]:
        if self._forges is None:
            self._forges = asyncio.ensure_future(
                self._factory.for_principal_with_unresolved(self._principal)
            )
        return await self._forges

    async def _forge_for(self, connection_id: str) -> VolundrPort:
        adapters, _ = await self._forges_of_principal()
        for adapter in adapters:
            if connection_id in {adapter.target_id, adapter.name}:
                return adapter
        raise ForgeSessionUnreadableError(
            f"Forge connection {connection_id!r} did not resolve, so its sessions cannot be "
            "read. Check the connection's credential in Guild."
        )

    async def _get_session(self, adapter: VolundrPort, session_id: str) -> VolundrSession | None:
        return await adapter.get_session(
            session_id, auth_token=self._auth_token, principal=self._principal
        )

    async def _locate(self, session_id: str) -> tuple[VolundrPort | None, VolundrSession | None]:
        adapters, unresolved = await self._forges_of_principal()
        if not adapters:
            raise ForgeSessionUnreadableError("No Forge connection is available to read sessions.")
        answers = await asyncio.gather(
            *(self._get_session(adapter, session_id) for adapter in adapters),
            return_exceptions=True,
        )
        failures = [answer for answer in answers if isinstance(answer, BaseException)]
        for failure in failures:
            if isinstance(failure, asyncio.CancelledError):
                raise failure
        for adapter, answer in zip(adapters, answers, strict=True):
            if not isinstance(answer, BaseException) and answer is not None:
                return adapter, answer
        if failures:
            raise ForgeSessionUnreadableError(
                f"Session {session_id} was not found, but a Forge could not be asked."
            ) from failures[0]
        if unresolved:
            raise ForgeSessionUnreadableError(
                f"Session {session_id} was not found, but {unresolved} registered Forge(s) "
                "could not be reached with a usable credential."
            )
        return None, None


def _forge_sessions(request: Request, principal: Principal) -> _ForgeSessions | None:
    factory = getattr(request.app.state, "volundr_factory", None)
    if factory is None:
        return None
    return _ForgeSessions(factory, principal=principal, auth_token=extract_bearer_token(request))


def _summary_response(item: WorkSummary) -> WorkSummaryResponse:
    return WorkSummaryResponse.model_validate(item)


def _coverage_response(item: SourceCoverage) -> SourceCoverageResponse:
    return SourceCoverageResponse.model_validate(item)


def _task_response(item: WorkTask) -> WorkTaskResponse:
    return WorkTaskResponse.model_validate(item)


def _detail_response(detail: WorkDetail) -> WorkDetailResponse:
    return WorkDetailResponse(
        item=_summary_response(detail.item),
        tasks=[_task_response(item) for item in detail.tasks],
        coverage=[_coverage_response(item) for item in detail.coverage],
        board=WorkBoardResponse.model_validate(detail.board) if detail.board else None,
    )


def create_work_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/work", tags=["Work"])

    @router.get("", response_model=WorkCollectionResponse)
    async def list_work(
        request: Request,
        execution_limit: Annotated[int, Query(alias="executionLimit", ge=1, le=200)] = 100,
        execution_cursor: Annotated[str, Query(alias="executionCursor")] = "",
        principal: Principal = Depends(extract_principal),
        saga_repo: SagaRepository = Depends(resolve_saga_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        tracker_resolution: TrackerResolution = Depends(resolve_work_trackers),
        execution_repo: WorkflowExecutionRepository | None = Depends(resolve_work_execution_repo),
    ) -> WorkCollectionResponse:
        try:
            result = await _service(
                principal=principal,
                saga_repo=saga_repo,
                campaign_repo=campaign_repo,
                tracker_resolution=tracker_resolution,
                execution_repo=execution_repo,
                campaign_lifecycle_available=bool(
                    getattr(request.app.state, "subscriber", None)
                    and request.app.state.subscriber.running
                ),
            ).list_work(
                execution_limit=execution_limit,
                execution_cursor=execution_cursor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkCollectionResponse(
            projects=[_summary_response(item) for item in result.projects],
            campaigns=[_summary_response(item) for item in result.campaigns],
            executions=[_summary_response(item) for item in result.executions],
            execution_next_cursor=result.execution_next_cursor,
            coverage=[_coverage_response(item) for item in result.coverage],
        )

    @router.get("/reviews", response_model=ReviewListResponse)
    async def list_reviews(
        principal: Principal = Depends(extract_principal),
        saga_repo: SagaRepository = Depends(resolve_saga_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> ReviewListResponse:
        """Every run a person reviews, across projects, from what campaigns already record."""
        campaigns = await campaign_repo.list_campaign_summaries(owner_id=principal.user_id)
        sagas = await saga_repo.list_sagas(owner_id=principal.user_id)
        return ReviewListResponse.model_validate({"items": review_list(campaigns, sagas)})

    @router.get("/review-widgets/{module}/{name}", response_model=ReviewWidgetResponse)
    async def read_review_widget(
        module: str,
        name: str,
        request: Request,
        _principal: Principal = Depends(extract_principal),
    ) -> ReviewWidgetResponse:
        """A widget's page as data: it only ever runs inside the review's sandbox."""
        widget = request.app.state.review_widgets.get(f"{module}/{name}")
        if widget is None:
            raise HTTPException(
                status_code=404,
                detail=f"Review widget {module}/{name} is not installed on this deployment",
            )
        return ReviewWidgetResponse.model_validate(widget)

    @router.get("/runs/{slug}", response_model=WorkDetailResponse)
    async def get_run(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        saga_repo: SagaRepository = Depends(resolve_saga_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> WorkDetailResponse:
        """One run by its slug, whatever page of Work it is on, finished or not."""
        campaign = await _owned_run(campaign_repo, slug, principal)
        # A run is read from its campaign alone: no tracker is asked.
        service = WorkReadService(
            principal=principal,
            saga_repo=saga_repo,
            campaign_repo=campaign_repo,
            trackers=[],
            execution_repo=None,
            campaign_lifecycle_available=bool(
                getattr(request.app.state, "subscriber", None)
                and request.app.state.subscriber.running
            ),
        )
        try:
            return _detail_response(await service.get_campaign(campaign.id))
        except WorkNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/{kind}/{work_id}", response_model=WorkDetailResponse)
    async def get_work(
        kind: Literal["project", "campaign", "execution"],
        work_id: UUID,
        request: Request,
        principal: Principal = Depends(extract_principal),
        saga_repo: SagaRepository = Depends(resolve_saga_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        tracker_resolution: TrackerResolution = Depends(resolve_work_trackers),
        execution_repo: WorkflowExecutionRepository | None = Depends(resolve_work_execution_repo),
    ) -> WorkDetailResponse:
        forge = await _forge_adapter(request, principal) if kind == "project" else None
        sessions = _forge_sessions(request, principal) if forge is not None else None
        service = _service(
            principal=principal,
            saga_repo=saga_repo,
            campaign_repo=campaign_repo,
            tracker_resolution=tracker_resolution,
            execution_repo=execution_repo,
            campaign_lifecycle_available=bool(
                getattr(request.app.state, "subscriber", None)
                and request.app.state.subscriber.running
            ),
            session_gates=sessions.read if sessions is not None else None,
            run_gates=_run_gate_reader(request),
            coordinator_repo=_coordinator_repo(request),
        )
        try:
            if kind == "campaign":
                return _detail_response(await service.get_campaign(work_id))
            if kind == "execution":
                return _detail_response(await service.get_execution(work_id))

            saga = await saga_repo.get_saga(work_id, owner_id=principal.user_id)
            if saga is None:
                raise WorkNotFoundError(f"Project work not found: {work_id}")
            ready_issue_ids: set[str] | None = None
            dispatch_coverage: SourceCoverage
            dispatch_service: DispatchService | None = getattr(
                request.app.state, "dispatch_service", None
            )
            if dispatch_service is None:
                dispatch_coverage = SourceCoverage(
                    source="dispatch",
                    connection_id=None,
                    status=CoverageStatus.UNAVAILABLE,
                    error=CoverageError(
                        code="notConfigured",
                        message="Dispatch readiness is not configured.",
                    ),
                )
            else:
                try:
                    ready = await dispatch_service.find_ready_issues(
                        principal.user_id,
                        principal=principal,
                        auth_token=extract_bearer_token(request),
                        saga_tracker_id=saga.tracker_id,
                        tracker_connection_id=saga.tracker_connection_id or None,
                    )
                    ready_issue_ids = {item.issue_id for item in ready}
                    dispatch_coverage = SourceCoverage(
                        source="dispatch",
                        connection_id=None,
                        status=CoverageStatus.COMPLETE,
                    )
                except (AuthorizationDeniedError, AuthorizationEvaluationError):
                    raise
                except Exception:
                    dispatch_coverage = SourceCoverage(
                        source="dispatch",
                        connection_id=None,
                        status=CoverageStatus.UNAVAILABLE,
                        error=CoverageError(
                            code="readinessReadFailed",
                            message="Dispatch readiness could not be determined.",
                        ),
                    )
            detail = await service.get_project(
                work_id,
                ready_issue_ids=ready_issue_ids,
                dispatch_coverage=dispatch_coverage,
            )
            return _detail_response(
                await _close_finished_coordinators(
                    detail,
                    saga_id=work_id,
                    request=request,
                )
            )
        except WorkNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkSourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post(
        "/project/{saga_id}/tasks/{issue_id}/gate-decision",
        response_model=GateDecisionResponse,
    )
    async def decide_gate(
        saga_id: UUID,
        issue_id: str,
        body: GateDecisionRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        saga_repo: SagaRepository = Depends(resolve_saga_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        tracker_resolution: TrackerResolution = Depends(resolve_work_trackers),
    ) -> GateDecisionResponse:
        """Answer the gate a ticket is waiting on, exactly as the reviewer saw it."""
        _require_changes_note(body)
        service = _service(
            principal=principal,
            saga_repo=saga_repo,
            campaign_repo=campaign_repo,
            tracker_resolution=tracker_resolution,
            execution_repo=None,
            campaign_lifecycle_available=True,
            run_gates=_run_gate_reader(request),
            coordinator_repo=_coordinator_repo(request),
        )
        try:
            detail = await service.get_project(saga_id)
        except WorkNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        task = next((item for item in detail.tasks if item.source.issue_id == issue_id), None)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Ticket not found: {issue_id}")
        gate = task.board.gate if task.board else None
        if gate is None or task.workflow_run is None:
            raise HTTPException(status_code=409, detail="This ticket is not waiting on a gate")
        if gate.id != body.gate_id or gate.activation_id != body.activation_id:
            raise HTTPException(
                status_code=409,
                detail="This gate changed after you opened it. Review it again.",
            )
        campaign = await _owned_run(campaign_repo, task.workflow_run.task_id, principal)
        return await _decide_shown_gate(request, campaign, body, principal)

    @router.get(
        "/project/{saga_id}/tasks/{issue_id}/reviews",
        response_model=TicketReviewsResponse,
    )
    async def ticket_reviews(
        saga_id: UUID,
        issue_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        saga_repo: SagaRepository = Depends(resolve_saga_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> TicketReviewsResponse:
        """Everything this ticket's runs have put in front of a person, oldest first."""
        saga = await saga_repo.get_saga(saga_id, owner_id=principal.user_id)
        if saga is None:
            raise HTTPException(status_code=404, detail=f"Project work not found: {saga_id}")
        campaigns = await campaign_repo.list_ticket_campaigns(
            owner_id=principal.user_id, saga_id=saga_id
        )
        runs = [
            campaign
            for campaign in campaigns
            if (ticket := ticket_of(campaign.metadata)) is not None and ticket[1] == issue_id
        ]
        reviews = await _read_reviews(runs, request)
        return TicketReviewsResponse.model_validate(
            {"reviews": reviews, "unreadable_runs": [], "plan_slug": saga.plan_slug}
        )

    @router.get("/runs/{slug}/reviews", response_model=RunReviewsResponse)
    async def run_review(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> RunReviewsResponse:
        """What a run has put in front of a person, the sessions it worked in, and its ticket."""
        campaign = await _owned_run(campaign_repo, slug, principal)
        state = await _workflow_runs(request).state(campaign.id)
        reviews = ledger_reviews(campaign, state)
        return RunReviewsResponse.model_validate(
            {"run": review_run(campaign, state), "reviews": reviews, "unreadable_runs": []}
        )

    @router.post("/runs/{slug}/gate-decision", response_model=GateDecisionResponse)
    async def decide_run_gate(
        slug: str,
        body: GateDecisionRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> GateDecisionResponse:
        """Answer the gate a run with no ticket is waiting on, exactly as the reviewer saw it."""
        _require_changes_note(body)
        campaign = await _owned_run(campaign_repo, slug, principal)
        return await _decide_shown_gate(request, campaign, body, principal)

    @router.post(
        "/project/{saga_id}/tasks/{issue_id}/start",
        response_model=TicketStartResponse,
    )
    async def start_ticket(
        saga_id: UUID,
        issue_id: str,
        request: Request,
        body: TicketStartRequest | None = None,
        principal: Principal = Depends(extract_principal),
        saga_repo: SagaRepository = Depends(resolve_saga_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        tracker_resolution: TrackerResolution = Depends(resolve_work_trackers),
    ) -> TicketStartResponse:
        """Start the workflow of the dropped column as a run of the ticket."""
        saga = await saga_repo.get_saga(saga_id, owner_id=principal.user_id)
        if saga is None:
            raise HTTPException(status_code=404, detail=f"Project work not found: {saga_id}")
        adapter = await _project_forge(request, principal, saga)
        if adapter is None:
            raise HTTPException(status_code=503, detail="No Forge connection is available")
        if saga.workflow_id is None and not saga.board_workflows:
            raise HTTPException(
                status_code=409,
                detail="Give this project a workflow before starting tickets",
            )
        gap = repository_gap(saga)
        if gap is not None:
            raise HTTPException(status_code=409, detail=gap)
        service = _service(
            principal=principal,
            saga_repo=saga_repo,
            campaign_repo=campaign_repo,
            tracker_resolution=tracker_resolution,
            execution_repo=None,
            campaign_lifecycle_available=True,
            run_gates=_run_gate_reader(request),
            coordinator_repo=_coordinator_repo(request),
        )
        try:
            issue = await service.tracker_issue(saga, issue_id)
            detail = await service.get_project(saga_id)
        except WorkSourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        task = next((item for item in detail.tasks if item.source.issue_id == issue_id), None)
        if issue is None or task is None:
            raise HTTPException(status_code=404, detail=f"Ticket not found: {issue_id}")
        _refuse_active_run(task)
        if detail.board is None:
            snapshot = saga.workflow_snapshot or {}
            workflow = TicketWorkflow(
                id=str(saga.workflow_id), name=str(snapshot.get("name") or "")
            )
        else:
            entered = _workflow_to_start(detail.board, task, body.column_id if body else None)
            workflow = TicketWorkflow(id=entered.id, name=entered.name)
        try:
            coordinator = await _coordinator_service(
                request, campaign_repo=campaign_repo, principal=principal
            ).start_workflow(saga, issue, workflow, connection_id=adapter.target_id)
        except WorkflowDocumentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RunStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TicketStartResponse(status="coordinating", session_id=coordinator.session_id)

    return router


__all__ = [
    "BoardColumnResponse",
    "BoardGateResponse",
    "BoardPlacementResponse",
    "DispatchRefResponse",
    "GateDecisionRequest",
    "GateDecisionResponse",
    "OperationalRunRefResponse",
    "RawWorkStateResponse",
    "ReviewItemResponse",
    "ReviewListResponse",
    "ReviewPanelResponse",
    "ReviewWidgetResponse",
    "ReviewRevisionResponse",
    "ReviewSourceResponse",
    "RunReviewResponse",
    "SessionRefResponse",
    "SourceCoverageResponse",
    "TicketReviewsResponse",
    "TicketStartRequest",
    "TrackerIssueRefResponse",
    "TrackerProjectRefResponse",
    "WorkAttentionResponse",
    "WorkBoardResponse",
    "WorkCollectionResponse",
    "WorkDetailResponse",
    "WorkLinksResponse",
    "WorkSummaryResponse",
    "WorkTaskResponse",
    "WorkflowRefResponse",
    "create_work_router",
    "resolve_work_execution_repo",
    "resolve_work_trackers",
]
