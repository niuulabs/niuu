"""Repository port for persisted Ting workflow catalogs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from ting.domain.models import WorkflowDefinition, WorkflowScope, WorkflowVersionSummary
from ting.domain.workflow_versioning import WorkflowVersionBump


class WorkflowRepository(ABC):
    """Persistence interface for stored workflow definitions."""

    @abstractmethod
    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        """List workflows visible to the owner, optionally filtered by scope."""

    @abstractmethod
    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None:
        """Fetch a workflow definition by ID."""

    @abstractmethod
    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        """Insert or update a workflow definition."""

    @abstractmethod
    async def list_workflow_versions(self, workflow_id: UUID) -> list[WorkflowVersionSummary]:
        """List immutable versions for one stable workflow identity."""

    @abstractmethod
    async def get_workflow_version(
        self,
        workflow_id: UUID,
        *,
        version: str | None = None,
        document_revision: str | None = None,
    ) -> WorkflowDefinition | None:
        """Fetch one exact immutable version by label or canonical revision."""

    @abstractmethod
    async def save_workflow_version(
        self,
        workflow: WorkflowDefinition,
        *,
        expected_revision: str | None,
        base_revision: str,
        bump: WorkflowVersionBump = "patch",
    ) -> WorkflowDefinition:
        """Atomically append a successor and advance the stable identity's head."""

    @abstractmethod
    async def delete_workflow(self, workflow_id: UUID) -> bool:
        """Delete a workflow definition by ID."""
