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

    @abstractmethod
    async def has_recorded_version_history(self, workflow_id: UUID) -> bool:
        """True once at least one immutable snapshot has actually been archived.

        Distinct from an empty ``list_workflow_versions`` result on adapters
        that synthesize the current head as a version when nothing is
        archived: this answers whether the versioned save path has ever
        actually written this identity. A row this returns False for was
        never touched by that path -- e.g. a pre-#1012 legacy system row
        reclassified by migration 000046 -- and cannot be safely compared by
        content hash against a current packaged definition (it predates
        persona pinning and schema versioning).
        """

    @abstractmethod
    async def adopt_legacy_bundled(self, seed: WorkflowDefinition) -> WorkflowDefinition:
        """Publish a packaged seed as the head of a never-versioned bundled row.

        Callers (``seed_system_workflows``) must only call this for a row
        with ``has_recorded_version_history() is False``. Implementations
        re-verify that condition themselves before writing, since a
        concurrent caller (another replica's own startup seeding) may have
        already adopted or advanced it.
        """

    @abstractmethod
    async def reclassify_orphaned_bundled_as_authored(
        self, workflow_id: UUID
    ) -> WorkflowDefinition | None:
        """Flip a never-versioned, package-orphaned bundled row to authored.

        For a row ``adopt_legacy_bundled`` cannot apply to (no packaged seed
        shares its id). Going through ``save_workflow``'s normal advance
        path would bump its version label -- crashing on a pre-#1012 row
        whose version predates the semantic-version requirement -- and
        archive a snapshot that then makes ``delete_workflow`` refuse it
        forever. This changes only ``version_origin``: no version change, no
        archive. Implementations re-verify the row is still never-versioned
        and bundled before writing. Returns the row's current state, or
        None if it no longer exists.
        """
