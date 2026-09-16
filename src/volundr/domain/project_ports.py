"""Persistence and filesystem boundaries for project coordination."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from volundr.domain.projects import ForgeProject, ProjectReceipt


class ProjectConflictError(ValueError):
    """A retry conflicts with already stored state."""


class ProjectRepository(ABC):
    @abstractmethod
    def dispatch_lock(self, key: UUID) -> AbstractAsyncContextManager[None]:
        """Serialize retries across API workers until a launch is persisted."""

    @abstractmethod
    async def claim_dispatch(self, key: UUID, fingerprint: str, session_id: UUID) -> bool:
        """Claim a stable launch identity; return whether it was completed before."""

    @abstractmethod
    async def complete_dispatch(self, key: UUID) -> None: ...

    @abstractmethod
    async def list(self, owner_id: str, tenant_id: str) -> list[ForgeProject]: ...

    @abstractmethod
    async def get(self, project_id: UUID) -> ForgeProject | None: ...

    @abstractmethod
    async def register(self, project: ForgeProject) -> ForgeProject: ...

    @abstractmethod
    async def update(self, project: ForgeProject, expected_revision: int) -> ForgeProject: ...

    @abstractmethod
    async def put_receipt(self, receipt: ProjectReceipt) -> ProjectReceipt: ...

    @abstractmethod
    async def receipts(self, project_id: UUID, after: int, limit: int) -> list[dict]: ...

    @abstractmethod
    async def acknowledge(self, project_id: UUID, receipt_id: UUID) -> bool: ...


class ProjectWorkspace(ABC):
    @abstractmethod
    async def discover(self, workspace_path: str) -> ForgeProject:
        """Inspect a local Git checkout and recover its stable project identity."""

    @abstractmethod
    async def context(self, project: ForgeProject) -> tuple[str, str]:
        """Return bounded project context and its Git revision."""

    @abstractmethod
    async def archive_receipt(self, project: ForgeProject, receipt: ProjectReceipt) -> None:
        """Save a durable handoff to the meta-repository without committing unrelated edits."""
