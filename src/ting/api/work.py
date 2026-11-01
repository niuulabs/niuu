"""Read-only Work projection over Ting's existing bounded contexts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from identity.ports import AuthorizationDeniedError, AuthorizationEvaluationError
from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.sagas import resolve_saga_repo
from ting.api.tracker import resolve_trackers
from ting.domain.services.dispatch_service import DispatchService
from ting.domain.services.work_read import (
    CoverageError,
    CoverageStatus,
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
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort, TrackerResolution
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_execution import WorkflowExecutionRepository


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


class WorkTaskResponse(_ResponseModel):
    source: TrackerIssueRefResponse
    title: str
    operational_run: OperationalRunRefResponse | None
    dispatch: DispatchRefResponse | None
    actions: list[str]


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
) -> WorkReadService:
    return WorkReadService(
        principal=principal,
        saga_repo=saga_repo,
        campaign_repo=campaign_repo,
        trackers=list(tracker_resolution.adapters),
        execution_repo=execution_repo,
        tracker_resolution_failures=tracker_resolution.failures,
        campaign_lifecycle_available=campaign_lifecycle_available,
    )


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
            return _detail_response(
                await service.get_project(
                    work_id,
                    ready_issue_ids=ready_issue_ids,
                    dispatch_coverage=dispatch_coverage,
                )
            )
        except WorkNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkSourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router


__all__ = [
    "DispatchRefResponse",
    "OperationalRunRefResponse",
    "RawWorkStateResponse",
    "SessionRefResponse",
    "SourceCoverageResponse",
    "TrackerIssueRefResponse",
    "TrackerProjectRefResponse",
    "WorkAttentionResponse",
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
