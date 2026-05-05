"""REST API for the dispatcher — queue and approve raids for execution.

Thin wrapper that delegates business logic to DispatchService.
Endpoints handle auth extraction, request validation, and response formatting.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.models import IntegrationType, Principal
from tyr.adapters.inbound.auth import extract_bearer_token, extract_principal
from tyr.api.flock_config import FlockPersonaResponse
from tyr.api.tracker import resolve_trackers
from tyr.domain.services.dispatch_service import (
    DispatchItem as ServiceDispatchItem,
)
from tyr.domain.services.dispatch_service import (
    DispatchResult as ServiceDispatchResult,
)
from tyr.domain.services.dispatch_service import (
    DispatchService,
)
from tyr.domain.services.dispatch_service import (
    QueueItem as ServiceQueueItem,
)
from tyr.ports.dispatcher_repository import DispatcherRepository
from tyr.ports.saga_repository import SagaRepository
from tyr.ports.volundr import VolundrFactory, VolundrPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic response / request models (API layer only)
# ---------------------------------------------------------------------------


class QueueItemResponse(BaseModel):
    """An issue ready for dispatch."""

    saga_id: str
    saga_name: str
    saga_slug: str
    repos: list[str]
    feature_branch: str
    phase_name: str
    issue_id: str
    identifier: str
    title: str
    description: str
    status: str
    priority: int = 0
    priority_label: str = ""
    estimate: float | None = None
    url: str = ""
    workflow_id: str | None = None
    workflow: str | None = None
    workflow_version: str | None = None


class ModelOption(BaseModel):
    id: str
    name: str


class DispatchConfigResponse(BaseModel):
    """Dispatch defaults from server config."""

    default_system_prompt: str = ""
    default_model: str = "claude-sonnet-4-6"
    models: list[ModelOption] = []
    flock_enabled: bool = False
    flock_default_personas: list[FlockPersonaResponse] = []
    flock_llm_config: dict = {}
    flock_sleipnir_publish_urls: list[str] = []


class DispatchRequest(BaseModel):
    """Request to dispatch selected issues."""

    items: list[DispatchItemRequest]
    model: str = Field(default="")
    system_prompt: str = Field(default="")
    connection_id: str | None = Field(
        default=None,
        description="Target a specific Volundr cluster by connection ID",
    )
    session_definition: str | None = Field(
        default=None,
        description="Optional Volundr session definition key (e.g. 'skuldCodex')",
    )
    workload_type: str = Field(
        default="solo",
        description="'solo' for a single Ravn session or 'ravn_flock' for a multi-agent flock",
    )
    workload_config: dict = Field(
        default_factory=dict,
        description="Flock config — passes 'personas' list to override server defaults",
    )


class DispatchItemRequest(BaseModel):
    """A single item to dispatch."""

    saga_id: str
    issue_id: str
    repo: str
    connection_id: str | None = Field(
        default=None,
        description="Target a specific Volundr cluster for this item (overrides request-level)",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Optional workflow override for this dispatch item",
    )
    session_definition: str | None = Field(
        default=None,
        description="Optional session definition override for this dispatch item",
    )


class DispatchResultResponse(BaseModel):
    """Result of dispatching a single item."""

    issue_id: str
    session_id: str
    session_name: str
    status: str
    cluster_name: str = ""


class DispatchBatchRequest(BaseModel):
    """Compatibility request for dispatching raids by raid tracker ID."""

    raid_ids: list[str] = Field(default_factory=list, min_length=1)


class FailedRaidDispatchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raid_id: str = Field(serialization_alias="raidId")
    reason: str


class DispatchBatchResultResponse(BaseModel):
    dispatched: list[str]
    failed: list[FailedRaidDispatchResponse]


class ClusterInfo(BaseModel):
    """A user's available Volundr cluster."""

    connection_id: str
    name: str
    url: str
    enabled: bool


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _queue_item_to_response(item: ServiceQueueItem) -> QueueItemResponse:
    return QueueItemResponse(
        saga_id=item.saga_id,
        saga_name=item.saga_name,
        saga_slug=item.saga_slug,
        repos=item.repos,
        feature_branch=item.feature_branch,
        phase_name=item.phase_name,
        issue_id=item.issue_id,
        identifier=item.identifier,
        title=item.title,
        description=item.description,
        status=item.status,
        priority=item.priority,
        priority_label=item.priority_label,
        estimate=item.estimate,
        url=item.url,
        workflow_id=item.workflow_id,
        workflow=item.workflow,
        workflow_version=item.workflow_version,
    )


def _result_to_response(result: ServiceDispatchResult) -> DispatchResultResponse:
    return DispatchResultResponse(
        issue_id=result.issue_id,
        session_id=result.session_id,
        session_name=result.session_name,
        status=result.status,
        cluster_name=result.cluster_name,
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def resolve_saga_repo() -> SagaRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Saga repository not configured",
    )


async def resolve_volundr() -> VolundrPort:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Volundr adapter not configured",
    )


async def resolve_volundr_factory() -> VolundrFactory:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Volundr factory not configured",
    )


async def resolve_dispatcher_repo() -> DispatcherRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Dispatcher repository not configured",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def resolve_dispatch_service() -> DispatchService:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Dispatch service not configured",
    )


async def _resolve_dispatch_item_for_raid(
    raid_id: str,
    *,
    trackers: list,
) -> tuple[ServiceDispatchItem | None, str | None]:
    for tracker in trackers:
        try:
            raid = await tracker.get_raid(raid_id)
        except Exception:
            continue

        saga = await tracker.get_saga_for_raid(raid.tracker_id)
        if saga is None:
            return None, "parent saga not found"
        if not saga.repos:
            return None, "saga has no repos"

        issues = await tracker.list_issues(saga.tracker_id)
        issue = next(
            (
                item
                for item in issues
                if item.id == raid.tracker_id or item.identifier == raid.tracker_id
            ),
            None,
        )
        if issue is None:
            return None, "raid issue not found in tracker"

        return (
            ServiceDispatchItem(
                saga_id=str(saga.id),
                issue_id=issue.id,
                repo=saga.repos[0],
                connection_id=None,
            ),
            None,
        )

    return None, "raid not found"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_dispatch_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/dispatch", tags=["Dispatcher"])

    @router.get("/config", response_model=DispatchConfigResponse)
    async def get_config(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> DispatchConfigResponse:
        """Get dispatch defaults from server configuration."""
        settings = request.app.state.settings
        flock = settings.dispatch.flock
        return DispatchConfigResponse(
            default_system_prompt=settings.dispatch.default_system_prompt,
            default_model=settings.dispatch.default_model,
            models=[ModelOption(id=m.id, name=m.name) for m in settings.ai_models],
            flock_enabled=flock.enabled,
            flock_default_personas=[
                FlockPersonaResponse(name=p.name, llm=dict(p.llm)) for p in flock.default_personas
            ],
            flock_llm_config=dict(flock.llm_config),
            flock_sleipnir_publish_urls=list(flock.sleipnir_publish_urls),
        )

    @router.get("/queue", response_model=list[QueueItemResponse])
    async def get_queue(
        request: Request,
        principal: Principal = Depends(extract_principal),
        service: DispatchService = Depends(resolve_dispatch_service),
    ) -> list[QueueItemResponse]:
        """Get the list of issues ready for dispatch across all sagas."""
        auth_token = extract_bearer_token(request)
        items = await service.find_ready_issues(principal.user_id, auth_token=auth_token)
        return [_queue_item_to_response(item) for item in items]

    @router.post("/approve", response_model=list[DispatchResultResponse])
    async def approve_dispatch(
        request: Request,
        body: DispatchRequest,
        principal: Principal = Depends(extract_principal),
        service: DispatchService = Depends(resolve_dispatch_service),
        dispatcher_repo: DispatcherRepository = Depends(resolve_dispatcher_repo),
    ) -> list[DispatchResultResponse]:
        """Approve and dispatch selected issues — spawns Volundr sessions."""
        # Ensure dispatcher state exists so the activity subscriber picks up this owner
        await dispatcher_repo.get_or_create(principal.user_id)

        auth_token = extract_bearer_token(request)
        settings = request.app.state.settings

        service_items = [
            ServiceDispatchItem(
                saga_id=item.saga_id,
                issue_id=item.issue_id,
                repo=item.repo,
                connection_id=item.connection_id,
                workflow_id=item.workflow_id,
                session_definition=item.session_definition,
            )
            for item in body.items
        ]

        persona_overrides: list[dict] | None = None
        if body.workload_type == "ravn_flock":
            raw = body.workload_config.get("personas")
            if isinstance(raw, list) and raw:
                persona_overrides = [{"name": p} if isinstance(p, str) else p for p in raw]

        results = await service.dispatch_issues(
            owner_id=principal.user_id,
            items=service_items,
            auth_token=auth_token,
            model=body.model or settings.dispatch.default_model,
            system_prompt=body.system_prompt or settings.dispatch.default_system_prompt,
            connection_id=body.connection_id,
            session_definition=body.session_definition,
            persona_overrides=persona_overrides,
        )
        return [_result_to_response(r) for r in results]

    @router.get("/clusters", response_model=list[ClusterInfo])
    async def list_clusters(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[ClusterInfo]:
        """List the user's available Volundr clusters from their CODE_FORGE connections."""
        integration_repo = getattr(request.app.state, "integration_repo", None)
        if integration_repo is None:
            return []

        connections = await integration_repo.list_connections(
            principal.user_id,
            integration_type=IntegrationType.CODE_FORGE,
        )
        clusters: list[ClusterInfo] = []
        for conn in connections:
            name = conn.config.get("name", "") or conn.slug or conn.id
            url = conn.config.get("url", "")
            clusters.append(
                ClusterInfo(
                    connection_id=conn.id,
                    name=name,
                    url=url,
                    enabled=conn.enabled,
                )
            )
        return clusters

    @router.post("/batch", response_model=DispatchBatchResultResponse)
    async def dispatch_batch(
        body: DispatchBatchRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        trackers: list = Depends(resolve_trackers),
        service: DispatchService = Depends(resolve_dispatch_service),
        dispatcher_repo: DispatcherRepository = Depends(resolve_dispatcher_repo),
    ) -> DispatchBatchResultResponse:
        """Compatibility alias for dispatching multiple raids by raid tracker ID."""
        await dispatcher_repo.get_or_create(principal.user_id)

        dispatch_items: list[ServiceDispatchItem] = []
        requested_raid_by_issue_id: dict[str, str] = {}
        failures: list[FailedRaidDispatchResponse] = []
        for raid_id in body.raid_ids:
            item, reason = await _resolve_dispatch_item_for_raid(raid_id, trackers=trackers)
            if item is None:
                failures.append(
                    FailedRaidDispatchResponse(
                        raid_id=raid_id,
                        reason=reason or "raid not found",
                    )
                )
                continue
            dispatch_items.append(item)
            requested_raid_by_issue_id[item.issue_id] = raid_id

        auth_token = extract_bearer_token(request)
        settings = request.app.state.settings
        results = await service.dispatch_issues(
            owner_id=principal.user_id,
            items=dispatch_items,
            auth_token=auth_token,
            model=settings.dispatch.default_model,
            system_prompt=settings.dispatch.default_system_prompt,
        )
        dispatched = [
            requested_raid_by_issue_id[result.issue_id]
            for result in results
            if result.issue_id in requested_raid_by_issue_id
        ]

        return DispatchBatchResultResponse(dispatched=dispatched, failed=failures)

    @router.post("/{raid_id}", status_code=status.HTTP_202_ACCEPTED)
    async def dispatch_raid(
        raid_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        trackers: list = Depends(resolve_trackers),
        service: DispatchService = Depends(resolve_dispatch_service),
        dispatcher_repo: DispatcherRepository = Depends(resolve_dispatcher_repo),
    ) -> Response:
        """Compatibility alias for dispatching a single raid by raid tracker ID."""
        await dispatcher_repo.get_or_create(principal.user_id)

        item, reason = await _resolve_dispatch_item_for_raid(raid_id, trackers=trackers)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=reason or "raid not found",
            )

        auth_token = extract_bearer_token(request)
        settings = request.app.state.settings
        await service.dispatch_issues(
            owner_id=principal.user_id,
            items=[item],
            auth_token=auth_token,
            model=settings.dispatch.default_model,
            system_prompt=settings.dispatch.default_system_prompt,
        )
        return Response(status_code=status.HTTP_202_ACCEPTED)

    return router
