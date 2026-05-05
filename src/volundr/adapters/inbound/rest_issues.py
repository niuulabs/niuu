"""FastAPI REST adapter for generic issue search/get/update across connected trackers."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import IntegrationType, Principal, TrackerIssue
from volundr.domain.ports import IntegrationRepository
from volundr.domain.services.tracker_factory import TrackerFactory

logger = logging.getLogger(__name__)


class StatusUpdateRequest(BaseModel):
    """Request body for updating an issue status."""

    status: str = Field(
        min_length=1,
        description="New status for the issue",
        examples=["In Progress"],
    )


def create_issues_router(
    integration_repo: IntegrationRepository,
    tracker_factory: TrackerFactory,
) -> APIRouter:
    """Create FastAPI router for generic issue endpoints."""
    return _build_issues_router(
        integration_repo,
        tracker_factory,
        prefix="/api/v1/volundr/issues",
        deprecated=True,
        canonical_prefix="/api/v1/tracker",
    )


def create_canonical_issues_router(
    integration_repo: IntegrationRepository,
    tracker_factory: TrackerFactory,
) -> APIRouter:
    """Create canonical tracker issue endpoints."""
    return _build_issues_router(
        integration_repo,
        tracker_factory,
        prefix="/api/v1/tracker",
        deprecated=False,
        canonical_prefix="/api/v1/tracker",
    )


def _build_issues_router(
    integration_repo: IntegrationRepository,
    tracker_factory: TrackerFactory,
    *,
    prefix: str,
    deprecated: bool,
    canonical_prefix: str,
) -> APIRouter:
    """Build either legacy or canonical generic issue endpoints."""
    router = APIRouter(
        prefix=prefix,
        tags=["Issues"],
    )

    search_path = "/search" if deprecated else "/issues"

    @router.get(search_path, response_model=list[TrackerIssue])
    async def search_issues(
        request: Request,
        response: Response,
        q: str = Query(description="Search query", min_length=1),
        project_id: str | None = Query(default=None, alias="projectId"),
        principal: Principal = Depends(extract_principal),
    ) -> list[TrackerIssue]:
        """Search issues across all connected issue trackers."""
        connections = await integration_repo.list_connections(
            principal.user_id,
            integration_type=IntegrationType.ISSUE_TRACKER,
        )
        if not connections:
            return []

        results: list[TrackerIssue] = []
        for conn in connections:
            if not conn.enabled:
                continue
            # Only load adapters from volundr's package
            if not conn.adapter.startswith("volundr."):
                continue
            try:
                adapter = await tracker_factory.create(conn)
                issues = await adapter.search_issues(q)
                results.extend(issues)
            except Exception:
                logger.warning(
                    "Issue search failed for connection %s",
                    conn.id,
                    exc_info=True,
                )
        if deprecated:
            warn_on_legacy_route(
                request,
                response,
                LegacyRouteNotice(
                    legacy_path=f"{prefix}/search",
                    canonical_path=f"{canonical_prefix}/issues",
                ),
                route_logger=logger,
            )
        return results

    get_path = "/{issue_id}" if deprecated else "/issues/{issue_id}"

    @router.get(get_path, response_model=TrackerIssue)
    async def get_issue(
        request: Request,
        response: Response,
        issue_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> TrackerIssue:
        """Get a single issue by ID, searching across connected trackers."""
        connections = await integration_repo.list_connections(
            principal.user_id,
            integration_type=IntegrationType.ISSUE_TRACKER,
        )
        for conn in connections:
            if not conn.enabled:
                continue
            if not conn.adapter.startswith("volundr."):
                continue
            try:
                adapter = await tracker_factory.create(conn)
                issue = await adapter.get_issue(issue_id)
                if issue is not None:
                    return issue
            except Exception:
                logger.warning(
                    "Issue get failed for connection %s",
                    conn.id,
                    exc_info=True,
                )
        if deprecated:
            warn_on_legacy_route(
                request,
                response,
                LegacyRouteNotice(
                    legacy_path=f"{prefix}/{issue_id}",
                    canonical_path=f"{canonical_prefix}/issues/{issue_id}",
                ),
                route_logger=logger,
            )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Issue not found: {issue_id}",
        )

    if deprecated:
        update_path = "/{issue_id}/status"
        update_decorator = router.post
    else:
        update_path = "/issues/{issue_id}"
        update_decorator = router.patch

    @update_decorator(update_path, response_model=TrackerIssue)
    async def update_issue_status(
        request: Request,
        response: Response,
        issue_id: str,
        data: StatusUpdateRequest,
        principal: Principal = Depends(extract_principal),
    ) -> TrackerIssue:
        """Update the status of an issue."""
        connections = await integration_repo.list_connections(
            principal.user_id,
            integration_type=IntegrationType.ISSUE_TRACKER,
        )
        for conn in connections:
            if not conn.enabled:
                continue
            try:
                adapter = await tracker_factory.create(conn)
                issue = await adapter.get_issue(issue_id)
                if issue is not None:
                    updated = await adapter.update_issue_status(issue_id, data.status)
                    return updated
            except Exception:
                logger.warning(
                    "Issue status update failed for connection %s",
                    conn.id,
                    exc_info=True,
                )
        if deprecated:
            warn_on_legacy_route(
                request,
                response,
                LegacyRouteNotice(
                    legacy_path=f"{prefix}/{issue_id}/status",
                    canonical_path=f"{canonical_prefix}/issues/{issue_id}",
                ),
                route_logger=logger,
            )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Issue not found: {issue_id}",
        )

    return router
