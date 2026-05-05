"""Repository port for persisted Tyr workflow catalogs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from tyr.domain.models import WorkflowDefinition, WorkflowScope


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
    async def delete_workflow(self, workflow_id: UUID) -> bool:
        """Delete a workflow definition by ID."""
