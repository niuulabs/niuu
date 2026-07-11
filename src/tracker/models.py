"""Shared issue tracker domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from niuu.domain.models import IntegrationConnection, IntegrationType


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TrackerIssue(BaseModel):
    """Issue from an external issue tracker."""

    id: str = Field(description="Internal issue ID from the tracker backend")
    identifier: str = Field(
        description="Human-readable issue identifier (e.g. NIU-57)"
    )
    title: str = Field(description="Issue title")
    status: str = Field(description="Current issue status (e.g. In Progress, Done)")
    assignee: str | None = Field(
        default=None,
        description="Display name of the assigned user",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Labels attached to the issue",
    )
    priority: int = Field(
        default=0,
        description="Priority level (0=none, 1=urgent, 4=low)",
    )
    url: str = Field(description="Web URL to view the issue in the tracker")

    model_config = {"frozen": False}


class ProjectMapping(BaseModel):
    """Map a git repository URL to an issue tracker project."""

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique mapping identifier",
    )
    repo_url: str = Field(description="Git repository URL to map")
    project_id: str = Field(description="Issue tracker project ID")
    project_name: str = Field(
        default="",
        description="Human-readable project name",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="Timestamp when the mapping was created",
    )

    model_config = {"frozen": False}


class TrackerConnectionStatus(BaseModel):
    """Connection status for an issue tracker."""

    connected: bool = Field(description="Whether the tracker connection is active")
    provider: str = Field(description="Tracker provider name (e.g. linear, jira)")
    workspace: str | None = Field(
        default=None,
        description="Workspace or organization name in the tracker",
    )
    user: str | None = Field(
        default=None,
        description="Authenticated user display name",
    )

    model_config = {"frozen": False}


__all__ = [
    "IntegrationConnection",
    "IntegrationType",
    "ProjectMapping",
    "TrackerConnectionStatus",
    "TrackerIssue",
]
