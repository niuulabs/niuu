"""Shared issue tracker ports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from niuu.ports.integrations import IntegrationRepository
from tracker.models import ProjectMapping, TrackerConnectionStatus, TrackerIssue


class IssueTrackerProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def check_connection(self) -> TrackerConnectionStatus: ...

    @abstractmethod
    async def search_issues(
        self, query: str, project_id: str | None = None
    ) -> list[TrackerIssue]: ...

    @abstractmethod
    async def get_recent_issues(self, project_id: str, limit: int = 10) -> list[TrackerIssue]: ...

    @abstractmethod
    async def get_issue(self, issue_id: str) -> TrackerIssue | None: ...

    @abstractmethod
    async def update_issue_status(self, issue_id: str, status: str) -> TrackerIssue: ...


class ProjectMappingRepository(ABC):
    @abstractmethod
    async def create(self, mapping: ProjectMapping) -> ProjectMapping: ...

    @abstractmethod
    async def list(self) -> list[ProjectMapping]: ...

    @abstractmethod
    async def get_by_repo(self, repo_url: str) -> ProjectMapping | None: ...

    @abstractmethod
    async def delete(self, mapping_id: UUID) -> bool: ...


__all__ = ["IntegrationRepository", "IssueTrackerProvider", "ProjectMappingRepository"]
