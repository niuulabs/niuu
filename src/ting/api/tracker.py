"""REST API for tracker browsing and import.

Thin REST layer — delegates all business logic to TrackerPort adapters.
The API receives pre-configured adapters (with credentials already resolved)
via a FastAPI dependency. Supports multiple trackers in parallel.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from niuu.domain.models import InstanceKind, Principal
from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from ting.adapters.inbound.auth import extract_principal
from ting.domain.models import (
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
    WorkflowScope,
)
from ting.domain.utils import _slugify
from ting.domain.workflow_snapshot import build_workflow_snapshot, workflow_name_from_snapshot
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort
from ting.ports.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)

_TERMINAL_PROJECT_STATUSES = {
    "complete",
    "completed",
    "done",
    "closed",
    "cancelled",
    "canceled",
    "archived",
    "merged",
}


def _is_terminal_project_status(status: str) -> bool:
    return status.strip().lower() in _TERMINAL_PROJECT_STATUSES


def _can_use_workflow(workflow, principal: Principal) -> bool:  # noqa: ANN001
    if workflow.scope == WorkflowScope.SYSTEM:
        return True
    return workflow.owner_id == principal.user_id


async def _resolve_import_workflow(
    *,
    request: Request,
    principal: Principal,
    workflow_id_value: str | None,
) -> tuple[UUID | None, str | None, dict | None]:
    if workflow_id_value is None:
        return None, None, None

    workflow_repo: WorkflowRepository | None = getattr(request.app.state, "workflow_repo", None)
    if workflow_repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow repository not configured",
        )

    try:
        workflow_id = UUID(workflow_id_value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid workflow_id: {workflow_id_value!r}",
        )

    workflow = await workflow_repo.get_workflow(workflow_id)
    if workflow is None or not _can_use_workflow(workflow, principal):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id_value}",
        )
    return workflow.id, workflow.version, build_workflow_snapshot(workflow)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ImportRequest(BaseModel):
    """Request body for importing a project as a saga."""

    project_id: str = Field(description="External tracker project ID")
    repos: list[str] = Field(description="Repositories (org/repo)")
    base_branch: str = Field(description="Branch to create feature branch from")
    workflow_id: str | None = Field(
        default=None,
        description="Optional saved workflow UUID to assign on import",
    )
    instance_id: str | None = Field(
        default=None,
        description="Optional Volundr target UUID to assign on import",
    )
    start_immediately: bool = Field(
        default=False,
        description="When true, assign a workflow and immediately dispatch ready work",
    )


class SagaResponse(BaseModel):
    """Response for a created saga."""

    id: str
    tracker_id: str
    name: str
    repos: list[str]
    feature_branch: str
    status: str
    phase_count: int
    run_count: int
    workflow_id: str | None = None
    workflow: str | None = None
    workflow_version: str | None = None
    instance_id: str | None = None
    instance_name: str | None = None
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dependency type — injected by the composition root
# ---------------------------------------------------------------------------


# This is the dependency function that main.py overrides to provide
# per-request TrackerPort adapters resolved from user credentials.
# The API layer never touches credentials directly.
async def resolve_trackers() -> list[TrackerPort]:
    """Default dependency — overridden by the composition root."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Tracker adapters not configured",
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_tracker_router() -> APIRouter:
    """Create FastAPI router for tracker browsing endpoints."""
    return _build_tracker_router(
        prefix="/api/v1/ting/tracker",
        deprecated=True,
        canonical_prefix="/api/v1/tracker",
    )


def create_canonical_tracker_router() -> APIRouter:
    """Create canonical tracker project browsing and import endpoints."""
    return _build_tracker_router(
        prefix="/api/v1/tracker",
        deprecated=False,
        canonical_prefix="/api/v1/tracker",
    )


def _build_tracker_router(
    *,
    prefix: str,
    deprecated: bool,
    canonical_prefix: str,
) -> APIRouter:
    """Build either legacy or canonical tracker project routes."""
    router = APIRouter(
        prefix=prefix,
        tags=["Tracker Browser"],
    )

    @router.get("/projects", response_model=list[TrackerProject])
    async def list_projects(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[TrackerProject]:
        """List all projects across all connected trackers."""
        results: list[TrackerProject] = []
        for adapter in adapters:
            try:
                projects = await adapter.list_projects()
                results.extend(
                    project
                    for project in projects
                    if not _is_terminal_project_status(project.status)
                )
            except Exception:
                logger.warning("list_projects failed for adapter", exc_info=True)
        if deprecated:
            warn_on_legacy_route(
                request,
                response,
                LegacyRouteNotice(
                    legacy_path=f"{prefix}/projects",
                    canonical_path=f"{canonical_prefix}/projects",
                ),
                route_logger=logger,
            )
        return results

    @router.get("/projects/{project_id}", response_model=TrackerProject)
    async def get_project(
        request: Request,
        response: Response,
        project_id: str,
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> TrackerProject:
        """Get a single project by ID, searching across connected trackers."""
        for adapter in adapters:
            try:
                project = await adapter.get_project(project_id)
                if deprecated:
                    warn_on_legacy_route(
                        request,
                        response,
                        LegacyRouteNotice(
                            legacy_path=f"{prefix}/projects/{project_id}",
                            canonical_path=f"{canonical_prefix}/projects/{project_id}",
                        ),
                        route_logger=logger,
                    )
                return project
            except Exception:
                continue
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    @router.get(
        "/projects/{project_id}/milestones",
        response_model=list[TrackerMilestone],
    )
    async def list_milestones(
        request: Request,
        response: Response,
        project_id: str,
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[TrackerMilestone]:
        """List milestones for a project."""
        for adapter in adapters:
            try:
                milestones = await adapter.list_milestones(project_id)
                if deprecated:
                    warn_on_legacy_route(
                        request,
                        response,
                        LegacyRouteNotice(
                            legacy_path=f"{prefix}/projects/{project_id}/milestones",
                            canonical_path=f"{canonical_prefix}/projects/{project_id}/milestones",
                        ),
                        route_logger=logger,
                    )
                return milestones
            except Exception:
                continue
        return []

    @router.get(
        "/projects/{project_id}/issues",
        response_model=list[TrackerIssue],
    )
    async def list_issues(
        request: Request,
        response: Response,
        project_id: str,
        milestone_id: str | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[TrackerIssue]:
        """List issues for a project, optionally filtered by milestone."""
        for adapter in adapters:
            try:
                issues = await adapter.list_issues(project_id, milestone_id)
                if deprecated:
                    warn_on_legacy_route(
                        request,
                        response,
                        LegacyRouteNotice(
                            legacy_path=f"{prefix}/projects/{project_id}/issues",
                            canonical_path=f"{canonical_prefix}/projects/{project_id}/issues",
                        ),
                        route_logger=logger,
                    )
                return issues
            except Exception:
                continue
        return []

    @router.post("/import", response_model=SagaResponse)
    async def import_project(
        request: Request,
        response: Response,
        body: ImportRequest,
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> SagaResponse:
        """Import a tracker project as a Saga reference.

        Only stores the link between the tracker project and Ting's
        execution context. All display data is fetched live from the
        tracker at read time.
        """
        project: TrackerProject | None = None
        for adapter in adapters:
            try:
                project = await adapter.get_project(body.project_id)
                break
            except Exception:
                continue

        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project not found: {body.project_id}",
            )

        workflow_id, workflow_version, workflow_snapshot = await _resolve_import_workflow(
            request=request,
            principal=principal,
            workflow_id_value=body.workflow_id,
        )
        instance_name: str | None = None
        if body.instance_id:
            instance_service = getattr(request.app.state, "instance_service", None)
            if instance_service is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Instance registry not configured",
                )
            instance = await instance_service.get_visible(principal, body.instance_id)
            if instance is None or instance.kind != InstanceKind.VOLUNDR or not instance.enabled:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Target not found: {body.instance_id}",
                )
            instance_name = instance.name
        if body.start_immediately and workflow_snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="start_immediately requires workflow_id",
            )

        dispatch_service = getattr(request.app.state, "dispatch_service", None)
        if body.start_immediately and dispatch_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Dispatch service not configured",
            )

        now = datetime.now(UTC)
        slug = project.slug or _slugify(project.name)
        saga_repo: SagaRepository = request.app.state.saga_repo
        owner_sagas = await saga_repo.list_sagas(owner_id=principal.user_id)
        existing = next((saga for saga in owner_sagas if saga.tracker_id == project.id), None)
        conflicting_slug = next((saga for saga in owner_sagas if saga.slug == slug), None)
        if conflicting_slug is not None and (
            existing is None or conflicting_slug.id != existing.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Saga with slug '{slug}' already exists",
            )

        saga = Saga(
            id=existing.id if existing is not None else uuid4(),
            tracker_id=project.id,
            tracker_type="linear",
            slug=slug,
            name=project.name,
            repos=body.repos,
            feature_branch=f"feat/{slug}",
            status=existing.status if existing is not None else SagaStatus.ACTIVE,
            confidence=existing.confidence if existing is not None else 0.0,
            created_at=existing.created_at if existing is not None else now,
            base_branch=body.base_branch,
            owner_id=principal.user_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workflow_snapshot=workflow_snapshot,
            instance_id=body.instance_id,
        )

        await saga_repo.save_saga(saga)

        warnings: list[str] = []
        if body.start_immediately and dispatch_service is not None:
            try:
                await dispatch_service.try_auto_continue(principal.user_id, saga.tracker_id)
            except Exception:
                msg = f"Failed to kick off initial dispatch for imported saga '{slug}'"
                logger.warning(msg, exc_info=True)
                warnings.append(msg)

        logger.info(
            "Imported saga '%s' from project %s",
            saga.name,
            project.id,
        )
        if deprecated:
            warn_on_legacy_route(
                request,
                response,
                LegacyRouteNotice(
                    legacy_path=f"{prefix}/import",
                    canonical_path=f"{canonical_prefix}/import",
                ),
                route_logger=logger,
            )

        return SagaResponse(
            id=str(saga.id),
            tracker_id=saga.tracker_id,
            name=saga.name,
            repos=saga.repos,
            feature_branch=saga.feature_branch,
            status=saga.status.value,
            phase_count=project.milestone_count,
            run_count=project.issue_count,
            workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
            workflow=workflow_name_from_snapshot(saga.workflow_snapshot),
            workflow_version=saga.workflow_version,
            instance_id=saga.instance_id,
            instance_name=instance_name,
            warnings=warnings,
        )

    return router
