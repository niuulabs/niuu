"""Domain service for issue tracker integration."""

from __future__ import annotations

import logging
from uuid import UUID

from tracker.factory import TrackerFactory
from tracker.models import (
    IntegrationType,
    ProjectMapping,
    TrackerConnectionStatus,
    TrackerIssue,
)
from tracker.ports import (
    IntegrationRepository,
    IssueTrackerProvider,
    ProjectMappingRepository,
)

logger = logging.getLogger(__name__)


class TrackerIssueNotFoundError(Exception):
    """Raised when a tracker issue is not found."""


class TrackerMappingNotFoundError(Exception):
    """Raised when a project mapping is not found."""


class TrackerService:
    """Service for issue tracker operations.

    Composes an IssueTrackerProvider (Linear, Jira, etc.) with a
    ProjectMappingRepository (repo URL -> project ID mappings).

    Supports a default provider (from config) and per-user integration
    connections resolved via TrackerFactory + IntegrationRepository.
    """

    def __init__(
        self,
        tracker: IssueTrackerProvider | None,
        mappings: ProjectMappingRepository,
        integration_repo: IntegrationRepository | None = None,
        tracker_factory: TrackerFactory | None = None,
    ):
        self._default_provider = tracker
        self._mappings = mappings
        self._integration_repo = integration_repo
        self._tracker_factory = tracker_factory

    async def _get_tracker_for_user(
        self,
        user_id: str | None = None,
    ) -> IssueTrackerProvider | None:
        """Resolve the issue tracker for a user.

        Checks for user-specific integration connections first,
        then falls back to the default provider from config.
        """
        if user_id and self._integration_repo and self._tracker_factory:
            connections = await self._integration_repo.list_connections(
                user_id,
                IntegrationType.ISSUE_TRACKER,
            )
            active = [c for c in connections if c.enabled]
            if active:
                return await self._tracker_factory.create(active[0])

        return self._default_provider

    def _require_tracker(
        self,
        tracker: IssueTrackerProvider | None,
    ) -> IssueTrackerProvider:
        """Ensure a tracker is available, raising if not."""
        if tracker is None:
            raise TrackerIssueNotFoundError("No issue tracker configured")
        return tracker

    async def check_connection(
        self,
        user_id: str | None = None,
    ) -> TrackerConnectionStatus:
        """Check the connection to the issue tracker."""
        tracker = await self._get_tracker_for_user(user_id)
        if tracker is None:
            return TrackerConnectionStatus(
                connected=False,
                provider="none",
            )
        return await tracker.check_connection()

    async def search_issues(
        self,
        query: str,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> list[TrackerIssue]:
        """Search issues by query string."""
        tracker = self._require_tracker(
            await self._get_tracker_for_user(user_id),
        )
        return await tracker.search_issues(query, project_id=project_id)

    async def get_recent_issues(
        self,
        project_id: str,
        limit: int = 10,
        user_id: str | None = None,
    ) -> list[TrackerIssue]:
        """Get recent issues for a project."""
        tracker = self._require_tracker(
            await self._get_tracker_for_user(user_id),
        )
        return await tracker.get_recent_issues(project_id, limit=limit)

    async def get_issue(
        self,
        issue_id: str,
        user_id: str | None = None,
    ) -> TrackerIssue:
        """Get a single issue by ID or identifier."""
        tracker = self._require_tracker(
            await self._get_tracker_for_user(user_id),
        )
        issue = await tracker.get_issue(issue_id)
        if issue is None:
            raise TrackerIssueNotFoundError(f"Issue not found: {issue_id}")
        return issue

    async def update_issue_status(
        self,
        issue_id: str,
        status: str,
        user_id: str | None = None,
    ) -> TrackerIssue:
        """Update the status of an issue."""
        tracker = self._require_tracker(
            await self._get_tracker_for_user(user_id),
        )
        return await tracker.update_issue_status(issue_id, status)

    # --- Project mapping operations ---

    async def create_mapping(
        self,
        repo_url: str,
        project_id: str,
        project_name: str = "",
    ) -> ProjectMapping:
        """Create a new repo-to-project mapping."""
        mapping = ProjectMapping(
            repo_url=repo_url,
            project_id=project_id,
            project_name=project_name,
        )
        created = await self._mappings.create(mapping)
        logger.info(
            "Created project mapping: repo=%s -> project=%s",
            repo_url,
            project_id,
        )
        return created

    async def list_mappings(self) -> list[ProjectMapping]:
        """List all project mappings."""
        return await self._mappings.list()

    async def get_mapping_by_repo(self, repo_url: str) -> ProjectMapping | None:
        """Get the mapping for a repo URL."""
        return await self._mappings.get_by_repo(repo_url)

    async def delete_mapping(self, mapping_id: UUID) -> bool:
        """Delete a project mapping."""
        deleted = await self._mappings.delete(mapping_id)
        if not deleted:
            raise TrackerMappingNotFoundError(f"Mapping not found: {mapping_id}")
        logger.info("Deleted project mapping: id=%s", mapping_id)
        return True
