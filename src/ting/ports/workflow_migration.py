"""Ports used by the explicit workflow catalog migration job."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from ravn.domain.persona_document import PortablePersonaSource
from ting.domain.models import WorkflowDefinition

WorkflowPersonaSourceResolver = Callable[
    [WorkflowDefinition], Awaitable[PortablePersonaSource]
]


class WorkflowMigrationSource(Protocol):
    async def list_all_workflows(self) -> list[WorkflowDefinition]: ...

    async def referenced_workflow_ids(self) -> set[UUID]: ...


class WorkflowMigrationTarget(Protocol):
    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None: ...

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition: ...

    async def authorize_bundled_replacements(self, workflow_ids: set[UUID]) -> None: ...

    async def mark_migration_complete(self, metadata: dict[str, Any]) -> None: ...
