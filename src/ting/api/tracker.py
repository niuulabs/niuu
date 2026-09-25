"""REST API for tracker browsing and import.

Thin REST layer — delegates all business logic to TrackerPort adapters.
The API receives pre-configured adapters (with credentials already resolved)
via a FastAPI dependency. Supports multiple trackers in parallel.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
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
from ting.domain.tracker_routing import (
    TrackerRoutingError,
    select_tracker,
    select_tracker_for_saga,
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
    workflow_version_value: str | None = None,
) -> tuple[UUID | None, str | None, dict | None]:
    if workflow_id_value is None:
        if workflow_version_value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="workflowVersion requires workflow_id",
            )
        return None, None, None

    workflow_repo: WorkflowRepository | None = getattr(request.app.state, "workflow_repo", None)
    if workflow_repo is not None:
        from ting.domain.services.resource_authorization import AuthorizedWorkflowRepository

        authorization = getattr(request.app.state, "authorization", None)
        if authorization is None:
            raise HTTPException(status_code=503, detail="Authorization is not configured")
        workflow_repo = AuthorizedWorkflowRepository(workflow_repo, authorization, principal)
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

    workflow = (
        await workflow_repo.get_workflow_version(
            workflow_id,
            version=workflow_version_value,
        )
        if workflow_version_value
        else await workflow_repo.get_workflow(workflow_id)
    )
    if workflow is None or not _can_use_workflow(workflow, principal):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id_value}",
        )
    persona_source = getattr(request.app.state, "persona_source", None)
    try:
        snapshot = build_workflow_snapshot(workflow, persona_source=persona_source)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return (
        workflow.id,
        workflow.version,
        snapshot,
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ImportRequest(BaseModel):
    """Request body for importing a project as a saga."""

    project_id: str = Field(description="External tracker project ID")
    tracker_connection_id: str | None = Field(
        default=None,
        description="Integration connection that owns the external project",
    )
    repos: list[str] = Field(description="Repositories (org/repo)")
    base_branch: str = Field(description="Branch to create feature branch from")
    repo_refs: list[dict[str, str]] = Field(
        default_factory=list,
        description="Per-repository branch bindings: [{repo, branch}]",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Optional saved workflow UUID to assign on import",
    )
    workflow_version: str | None = Field(
        default=None,
        alias="workflowVersion",
        description="Optional immutable workflow version to assign on import",
    )
    instance_id: str | None = Field(
        default=None,
        description="Optional Volundr target UUID to assign on import",
    )
    target_tags: list[str] = Field(
        default_factory=list,
        description="Optional Volundr target tags for label-based dispatch routing",
    )
    target_match: str = Field(
        default="all",
        description="Tag match mode for target_tags: all or any",
    )
    start_immediately: bool = Field(
        default=False,
        description="When true, assign a workflow and immediately dispatch ready work",
    )

    model_config = {"populate_by_name": True}


class SagaResponse(BaseModel):
    """Response for a created saga."""

    id: str
    tracker_id: str
    tracker_connection_id: str = ""
    tracker_type: str = ""
    name: str
    repos: list[str]
    base_branch: str = "main"
    feature_branch: str
    status: str
    phase_count: int
    run_count: int
    workflow_id: str | None = None
    workflow: str | None = None
    workflow_version: str | None = None
    instance_id: str | None = None
    instance_name: str | None = None
    target_tags: list[str] = Field(default_factory=list)
    target_match: str = "all"
    repo_branches: dict[str, str] = Field(default_factory=dict)
    repo_refs: list[dict[str, str]] = Field(default_factory=list)
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


def _provider(adapter: TrackerPort) -> str:
    if adapter.provider:
        return adapter.provider
    name = type(adapter).__name__.lower()
    for provider in ("jira", "linear", "native"):
        if provider in name:
            return provider
    return name


def _with_project_source(project: TrackerProject, adapter: TrackerPort) -> TrackerProject:
    return replace(
        project,
        tracker_connection_id=adapter.connection_id,
        tracker_type=_provider(adapter),
        tracker_name=adapter.connection_name or _provider(adapter),
    )


def _with_milestone_source(milestone: TrackerMilestone, adapter: TrackerPort) -> TrackerMilestone:
    return replace(
        milestone,
        tracker_connection_id=adapter.connection_id,
        tracker_type=_provider(adapter),
        tracker_name=adapter.connection_name or _provider(adapter),
    )


def _with_issue_source(issue: TrackerIssue, adapter: TrackerPort) -> TrackerIssue:
    return replace(
        issue,
        tracker_connection_id=adapter.connection_id,
        tracker_type=_provider(adapter),
        tracker_name=adapter.connection_name or _provider(adapter),
    )


async def _resolve_project_source(
    adapters: list[TrackerPort],
    project_id: str,
    connection_id: str | None,
) -> tuple[TrackerPort, TrackerProject]:
    if connection_id:
        try:
            adapter = select_tracker(adapters, connection_id=connection_id)
        except TrackerRoutingError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        try:
            return adapter, await adapter.get_project(project_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project not found on tracker connection '{connection_id}': {project_id}",
            ) from exc

    matches: list[tuple[TrackerPort, TrackerProject]] = []
    for adapter in adapters:
        try:
            matches.append((adapter, await adapter.get_project(project_id)))
        except Exception:
            continue
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Project ID '{project_id}' exists in multiple tracker connections; "
                "tracker_connection_id is required"
            ),
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Project not found: {project_id}",
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
                    _with_project_source(project, adapter)
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
        tracker_connection_id: str | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> TrackerProject:
        """Get a single project by ID, searching across connected trackers."""
        adapter, project = await _resolve_project_source(
            adapters, project_id, tracker_connection_id
        )
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
        return _with_project_source(project, adapter)

    @router.get(
        "/projects/{project_id}/milestones",
        response_model=list[TrackerMilestone],
    )
    async def list_milestones(
        request: Request,
        response: Response,
        project_id: str,
        tracker_connection_id: str | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[TrackerMilestone]:
        """List milestones for a project."""
        adapter, _ = await _resolve_project_source(adapters, project_id, tracker_connection_id)
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
        return [_with_milestone_source(item, adapter) for item in milestones]

    @router.get(
        "/projects/{project_id}/issues",
        response_model=list[TrackerIssue],
    )
    async def list_issues(
        request: Request,
        response: Response,
        project_id: str,
        milestone_id: str | None = Query(default=None),
        tracker_connection_id: str | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[TrackerIssue]:
        """List issues for a project, optionally filtered by milestone."""
        adapter, _ = await _resolve_project_source(adapters, project_id, tracker_connection_id)
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
        return [_with_issue_source(item, adapter) for item in issues]

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
        adapter, project = await _resolve_project_source(
            adapters, body.project_id, body.tracker_connection_id
        )

        workflow_id, workflow_version, workflow_snapshot = await _resolve_import_workflow(
            request=request,
            principal=principal,
            workflow_id_value=body.workflow_id,
            workflow_version_value=body.workflow_version,
        )
        repo_branches = {
            str(ref.get("repo") or "").strip(): str(ref.get("branch") or "").strip()
            for ref in body.repo_refs
            if str(ref.get("repo") or "").strip() and str(ref.get("branch") or "").strip()
        }
        repos = list(repo_branches) if repo_branches else body.repos
        base_branch = next(iter(repo_branches.values()), body.base_branch)
        target_tags = [tag.strip() for tag in body.target_tags if tag.strip()]
        target_match = body.target_match if body.target_match in {"all", "any"} else "all"

        instance_name: str | None = None
        if body.instance_id and not target_tags:
            instance_registry = getattr(request.app.state, "instance_registry", None)
            if instance_registry is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Instance registry not configured",
                )
            instance = await instance_registry.get_volundr_target(principal, body.instance_id)
            if instance is None:
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
        from ting.domain.services.resource_authorization import AuthorizedSagaRepository

        authorization = getattr(request.app.state, "authorization", None)
        if authorization is None:
            raise HTTPException(status_code=503, detail="Authorization is not configured")
        saga_repo: SagaRepository = AuthorizedSagaRepository(
            request.app.state.saga_repo, authorization, principal, read_action="update"
        )
        owner_sagas = await saga_repo.list_sagas(owner_id=principal.user_id)

        def _belongs_to_selected_connection(candidate: Saga) -> bool:
            if candidate.tracker_id != project.id:
                return False
            if candidate.tracker_connection_id:
                return candidate.tracker_connection_id == adapter.connection_id
            try:
                return select_tracker_for_saga(adapters, candidate) is adapter
            except TrackerRoutingError:
                return False

        existing = next(
            (saga for saga in owner_sagas if _belongs_to_selected_connection(saga)),
            None,
        )
        conflicting_slug = next((saga for saga in owner_sagas if saga.slug == slug), None)
        if conflicting_slug is not None and (
            existing is None or conflicting_slug.id != existing.id
        ):
            try:
                conflicting_adapter = select_tracker_for_saga(adapters, conflicting_slug)
            except TrackerRoutingError:
                conflicting_adapter = None
            if conflicting_adapter is None or conflicting_adapter is adapter:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Saga with slug '{slug}' already exists on this tracker connection",
                )
            slug = f"{slug}-{_provider(adapter)}"
            if any(saga.slug == slug for saga in owner_sagas):
                suffix = adapter.connection_id[:8] or "tracker"
                slug = f"{slug}-{suffix}"

        saga = Saga(
            tenant_id=existing.tenant_id if existing is not None else principal.tenant_id,
            id=existing.id if existing is not None else uuid4(),
            tracker_id=project.id,
            tracker_type=_provider(adapter),
            tracker_connection_id=adapter.connection_id,
            slug=slug,
            name=project.name,
            repos=repos,
            repo_branches=repo_branches,
            feature_branch=f"feat/{slug}",
            status=existing.status if existing is not None else SagaStatus.ACTIVE,
            created_at=existing.created_at if existing is not None else now,
            base_branch=base_branch,
            owner_id=principal.user_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workflow_snapshot=workflow_snapshot,
            instance_id=None if target_tags else body.instance_id,
            target_tags=target_tags,
            target_match=target_match,
        )

        await saga_repo.save_saga(saga)

        warnings: list[str] = []
        if body.start_immediately and dispatch_service is not None:
            try:
                if saga.tracker_connection_id:
                    await dispatch_service.try_auto_continue(
                        principal.user_id,
                        saga.tracker_id,
                        tracker_connection_id=saga.tracker_connection_id,
                    )
                else:
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
            tracker_connection_id=saga.tracker_connection_id,
            tracker_type=saga.tracker_type,
            name=saga.name,
            repos=saga.repos,
            base_branch=saga.base_branch,
            feature_branch=saga.feature_branch,
            status=saga.status.value,
            phase_count=project.milestone_count,
            run_count=project.issue_count,
            workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
            workflow=workflow_name_from_snapshot(saga.workflow_snapshot),
            workflow_version=saga.workflow_version,
            instance_id=saga.instance_id,
            instance_name=instance_name,
            target_tags=saga.target_tags,
            target_match=saga.target_match,
            repo_branches=saga.repo_branches,
            repo_refs=[
                {"repo": repo, "branch": saga.repo_branches.get(repo, saga.base_branch)}
                for repo in saga.repos
            ],
            warnings=warnings,
        )

    return router
