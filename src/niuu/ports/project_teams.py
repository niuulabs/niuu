"""Where a Guild keeps the teams of its Forges' projects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from niuu.domain.project_team import ProjectTeamRecord


class ProjectTeamHomeConflictError(RuntimeError):
    """Another Forge already publishes this project's team; it is never taken over."""


class ProjectTeamRecords(ABC):
    @abstractmethod
    async def get(self, project_id: UUID) -> ProjectTeamRecord | None:
        """The newest team published for the project, or ``None``: none was yet."""

    @abstractmethod
    async def put(self, record: ProjectTeamRecord) -> ProjectTeamRecord:
        """Keep the team unless a newer version is kept already; answer the one kept.

        The first publication decides which Forge is the project's home. Raises
        ``ProjectTeamHomeConflictError`` for a publication by any other Forge.
        """

    @abstractmethod
    async def of_member(self, user_id: str, tenant_id: str) -> list[ProjectTeamRecord]:
        """The records of every project of the org that the person is on."""

    @abstractmethod
    async def recorded(self, project_ids: list[UUID]) -> set[UUID]:
        """Which of the projects have a record, whoever is on them."""
