"""Work projection over Ting's existing bounded contexts.

Everything here reads, apart from the gate decision, which only forwards an
answer to the session that owns the gate.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from identity.ports import AuthorizationDeniedError, AuthorizationEvaluationError
from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.sagas import resolve_saga_repo
from ting.api.tracker import resolve_trackers
from ting.domain.models import WorkflowCampaignStatus
from ting.domain.services.dispatch_service import DispatchService
from ting.domain.services.ticket_coordinator import (
    TicketCoordinatorService,
    TicketWorkflow,
    coordinator_ravn_config,
)
from ting.domain.services.work_read import (
    CoverageError,
    CoverageStatus,
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
from ting.domain.ticket_coordinator import TicketCoordinatorStatus
from ting.domain.workflow_board import (
    BACKLOG_COLUMN_ID,
    BoardColumnKind,
    BoardTicketState,
    BoardWorkflow,
    ProjectBoard,
)
from ting.ports.saga_repository import SagaRepository
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.tracker import TrackerPort, TrackerResolution
from ting.ports.volundr import VolundrPort, VolundrSession
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_execution import WorkflowExecutionRepository

_GATE_DECISIONS = {"approve": "APPROVE", "request_changes": "CHANGES_REQUESTED"}
_GATE_DECISION_SOURCE = "ting.board"
_SESSION_ERROR_STATE = "error"
_SESSION_STARTING_REASON = "The run's session is starting. Its gate returns when it is up."
_SESSION_STOPPED_REASON = "The run's session is stopped. Start it again to answer its gate."
_SESSION_AWAY_REASONS = {
    "created": _SESSION_STARTING_REASON,
    "starting": _SESSION_STARTING_REASON,
    "provisioning": _SESSION_STARTING_REASON,
    "stopping": _SESSION_STOPPED_REASON,
    "stopped": _SESSION_STOPPED_REASON,
}
_BLOCKED_VERDICT = "blocked"
_CLOSED_TICKET_STATUS_TYPES = frozenset({"completed", "canceled"})
# Starting is here because no run exists yet: a coordinator that never acted can be asked again.
_STARTABLE_STATES = frozenset(
    {BoardTicketState.NOT_STARTED, BoardTicketState.READY, BoardTicketState.STARTING}
)

logger = logging.getLogger(__name__)
_PASSED_THROUGH_GATE_STATUSES = frozenset({403, 404, 409})


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
    operational_run: OperationalRunRefResponse | None
    dispatch: DispatchRefResponse | None
    actions: list[str]
    board: BoardPlacementResponse | None = None
    coordinator: TicketCoordinatorRefResponse | None = None
    workflow_run: TicketWorkflowRunResponse | None = None


class BoardColumnResponse(_ResponseModel):
    id: str
    title: str
    kind: BoardColumnKind
    review: str | None = None
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
        coordinator_repo=coordinator_repo,
    )


def _run_session_id(task: WorkTask) -> str | None:
    """The session a ticket's gates live in, whichever way its run started."""
    if task.workflow_run is not None:
        return task.workflow_run.session_id
    return task.operational_run.session_id if task.operational_run else None


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
    if (
        placement.state == BoardTicketState.BLOCKED
        and run is None
        and task.operational_run is None
        and task.coordinator is not None
    ):
        # The coordinator died before any run began, so there is nothing to wait for.
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


def _public_origin(request: Request) -> str:
    configured = request.app.state.settings.a2a.public_base_url
    return (configured or str(request.base_url)).rstrip("/")


def _coordinator_service(
    request: Request,
    *,
    adapter: VolundrPort,
    principal: Principal,
) -> TicketCoordinatorService:
    repo = _coordinator_repo(request)
    if repo is None:
        raise HTTPException(status_code=503, detail="Ticket coordinators are not configured")
    settings = request.app.state.settings.ticket_coordinator
    return TicketCoordinatorService(
        repo=repo,
        volundr=adapter,
        principal=principal,
        auth_token=extract_bearer_token(request),
        persona=settings.persona,
        model=settings.model,
        session_definitions=request.app.state.settings.session_definitions,
        configured_models=list(request.app.state.settings.bifrost.models),
        cli_turn_timeout_seconds=(
            request.app.state.settings.dispatch.workflow_cli_turn_timeout_seconds
        ),
        ravn_config=coordinator_ravn_config(
            request.app.state.settings.dispatch.flock.ravn_config, settings.ravn_config
        ),
        platform_origin=_public_origin(request),
        anonymous_platform=request.app.state.settings.auth.allow_anonymous_dev,
    )


async def _close_finished_coordinators(
    detail: WorkDetail,
    *,
    saga_id: UUID,
    request: Request,
    adapter: VolundrPort | None,
    principal: Principal,
) -> WorkDetail:
    """A coordinator lives as long as its ticket, so a closed ticket ends it."""
    finished = [
        task
        for task in detail.tasks
        if task.coordinator is not None
        and task.coordinator.status == TicketCoordinatorStatus.ACTIVE
        and task.source.status_type.strip().lower() in _CLOSED_TICKET_STATUS_TYPES
    ]
    if not finished or adapter is None:
        return detail
    service = _coordinator_service(request, adapter=adapter, principal=principal)
    repo = _coordinator_repo(request)
    failed = False
    for task in finished:
        coordinator = await repo.get(saga_id, task.source.issue_id)
        try:
            await service.close(coordinator)
        except httpx.HTTPError:
            logger.error(
                "Could not close the coordinator of %s", task.source.identifier, exc_info=True
            )
            failed = True
    if not failed:
        return detail
    return replace(
        detail,
        coverage=(
            *detail.coverage,
            SourceCoverage(
                source="ticketCoordinators",
                connection_id=None,
                status=CoverageStatus.PARTIAL,
                error=CoverageError(
                    code="closeFailed",
                    message="A finished ticket's coordinator session could not be closed.",
                ),
            ),
        ),
    )


async def _forge_adapter(request: Request, principal: Principal) -> VolundrPort | None:
    factory = getattr(request.app.state, "volundr_factory", None)
    if factory is None:
        return None
    return await factory.primary_for_principal(principal)


def _blocked_reason(session: VolundrSession | None) -> str | None:
    """Why a flock stopped, when its last outcome before going idle was a blocked verdict."""
    metadata = getattr(session, "activity_metadata", None)
    last = metadata.get("flock_idle") if isinstance(metadata, dict) else None
    if not isinstance(last, dict) or last.get("verdict") != _BLOCKED_VERDICT:
        return None
    return str(last.get("summary") or f"{last.get('persona') or 'A stage'} is blocked.")


def _session_gate_reader(
    adapter: VolundrPort | None,
    *,
    request: Request,
    principal: Principal,
) -> SessionGateReader | None:
    if adapter is None:
        return None
    auth_token = extract_bearer_token(request)

    async def read(session_id: str) -> SessionProgress:
        session = await adapter.get_session(session_id, auth_token=auth_token, principal=principal)
        if session is not None and session.status in _SESSION_AWAY_REASONS:
            # Its broker is not listening, so its gates cannot be read right now.
            return SessionProgress(
                gates=[],
                chat_endpoint=session.chat_endpoint,
                away_reason=_SESSION_AWAY_REASONS[session.status],
                blocked_reason=(
                    _SESSION_STOPPED_REASON
                    if _SESSION_AWAY_REASONS[session.status] == _SESSION_STOPPED_REASON
                    else None
                ),
            )
        gates = await adapter.get_workflow_gates(
            session_id, auth_token=auth_token, principal=principal
        )
        return SessionProgress(
            gates=gates,
            failed=session is not None and session.activity_state == _SESSION_ERROR_STATE,
            chat_endpoint=session.chat_endpoint if session is not None else None,
            blocked_reason=_blocked_reason(session),
        )

    return read


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
            session_gates=_session_gate_reader(
                forge if kind == "project" else None,
                request=request,
                principal=principal,
            ),
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
                    adapter=forge,
                    principal=principal,
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
        notes = body.notes.strip()
        if body.decision == "request_changes" and not notes:
            raise HTTPException(status_code=422, detail="Say what should change")
        adapter = await _forge_adapter(request, principal)
        if adapter is None:
            raise HTTPException(status_code=503, detail="No Forge connection is available")
        service = _service(
            principal=principal,
            saga_repo=saga_repo,
            campaign_repo=campaign_repo,
            tracker_resolution=tracker_resolution,
            execution_repo=None,
            campaign_lifecycle_available=True,
            session_gates=_session_gate_reader(adapter, request=request, principal=principal),
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
        session_id = _run_session_id(task)
        if gate is None or session_id is None:
            raise HTTPException(status_code=409, detail="This ticket is not waiting on a gate")
        if gate.id != body.gate_id or gate.activation_id != body.activation_id:
            raise HTTPException(
                status_code=409,
                detail="This gate changed after you opened it. Review it again.",
            )
        try:
            await adapter.resolve_workflow_gate(
                session_id,
                gate.id,
                _GATE_DECISIONS[body.decision],
                notes=notes,
                source=_GATE_DECISION_SOURCE,
                auth_token=extract_bearer_token(request),
                principal=principal,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise HTTPException(
                status_code=status_code if status_code in _PASSED_THROUGH_GATE_STATUSES else 502,
                detail=f"The session did not accept this decision: {exc}",
            ) from exc
        return GateDecisionResponse(status="resolved", session_id=session_id, gate_id=gate.id)

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
        """Hand a ticket to its coordinator, which starts the workflow of the dropped column."""
        adapter = await _forge_adapter(request, principal)
        if adapter is None:
            raise HTTPException(status_code=503, detail="No Forge connection is available")
        saga = await saga_repo.get_saga(saga_id, owner_id=principal.user_id)
        if saga is None:
            raise HTTPException(status_code=404, detail=f"Project work not found: {saga_id}")
        if (saga.workflow_id is None and not saga.board_workflows) or not saga.repos:
            raise HTTPException(
                status_code=409,
                detail="Give this project a workflow and a repository before starting tickets",
            )
        service = _service(
            principal=principal,
            saga_repo=saga_repo,
            campaign_repo=campaign_repo,
            tracker_resolution=tracker_resolution,
            execution_repo=None,
            campaign_lifecycle_available=True,
            session_gates=_session_gate_reader(adapter, request=request, principal=principal),
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
                request, adapter=adapter, principal=principal
            ).start_workflow(saga, issue, workflow)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"The ticket's coordinator could not be started: {exc}",
            ) from exc
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
    "SessionRefResponse",
    "SourceCoverageResponse",
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
