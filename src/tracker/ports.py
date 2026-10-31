"""Shared issue tracker ports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from niuu.ports.integrations import IntegrationRepository
from tracker.models import ProjectMapping, TrackerConnectionStatus, TrackerIssue


class TrackerRequestError(ValueError):
    """The tracker cannot serve the request as asked.

    Raised for caller-correctable problems — an unknown team, project, or
    label, or a target outside the connection's configured scope — so callers
    can tell "fix your input" apart from "the tracker is broken".
    """


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

    @abstractmethod
    async def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> TrackerIssue:
        """Rewrite an issue's title and/or description and return it.

        ``description`` replaces the whole body (Markdown). Fields left as
        ``None`` are not touched. Raises :class:`TrackerRequestError` when the
        issue is outside the connection's scope or the tracker rejects the edit.
        """

    @abstractmethod
    async def create_issue(
        self,
        title: str,
        description: str,
        *,
        team: str,
        labels: list[str] | None = None,
        priority: int | None = None,
    ) -> TrackerIssue:
        """Create an issue and return it, including its description.

        ``team`` is the tracker's container key (a Linear team key such as
        ``NIU``, a Jira project key). ``labels`` are resolved by name and
        ``priority`` uses the TrackerIssue scale (0=none, 1=urgent … 4=low).
        Raises :class:`TrackerRequestError` when the team or any label does
        not exist — nothing requested is ever silently dropped.
        """


class ProjectMappingRepository(ABC):
    @abstractmethod
    async def create(self, mapping: ProjectMapping) -> ProjectMapping: ...

    @abstractmethod
    async def list(self) -> list[ProjectMapping]: ...

    @abstractmethod
    async def get_by_repo(self, repo_url: str) -> ProjectMapping | None: ...

    @abstractmethod
    async def delete(self, mapping_id: UUID) -> bool: ...


__all__ = [
    "IntegrationRepository",
    "IssueTrackerProvider",
    "ProjectMappingRepository",
    "TrackerRequestError",
]
