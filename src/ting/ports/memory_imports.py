"""Ports of the memory import: its records, the tracker read, the gateway and the compile run."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from ting.domain.memory_imports import (
    MemoryImport,
    SourceAccepted,
    SourceWrite,
    UpdatedIssue,
)


class MemoryImportRepository(ABC):
    """Persistence of memory imports."""

    @abstractmethod
    async def create(self, record: MemoryImport) -> MemoryImport:
        """Store a new import. Raises ``MemoryImportExistsError`` for a duplicate source."""

    @abstractmethod
    async def get(self, import_id: UUID) -> MemoryImport | None:
        """One import, or ``None``."""

    @abstractmethod
    async def list_for_project(self, project_id: UUID) -> list[MemoryImport]:
        """The project's imports, oldest first."""

    @abstractmethod
    async def list_all(self) -> list[MemoryImport]:
        """Every import, oldest first: what the loop runs."""

    @abstractmethod
    async def delete(self, import_id: UUID) -> bool:
        """Delete an import; ``False`` when there was none."""

    @abstractmethod
    async def record_progress(
        self,
        import_id: UUID,
        *,
        cursor: datetime | None,
        add_pending: Sequence[str],
    ) -> None:
        """Move the cursor forward (never back) and add sources awaiting their compile run."""

    @abstractmethod
    async def clear_pending(self, import_id: UUID, source_ids: Sequence[str]) -> None:
        """Forget sources a compile run was started for."""

    @abstractmethod
    async def record_run(self, import_id: UUID, *, ran_at: datetime, error: str) -> None:
        """Stamp a run; ``error`` is empty when it succeeded."""


class IssueUpdateReader(ABC):
    """Reads the tickets of one tracker project that changed after a moment, with comments."""

    @abstractmethod
    async def issues_updated_since(
        self, tracker_project_id: str, since: datetime | None
    ) -> list[UpdatedIssue]:
        """Every ticket of the project updated after ``since`` (all when ``None``), oldest
        change first, each with all its comments. Pages to exhaustion; raises on any failure.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release the connection."""


class ImportConnections(ABC):
    """Resolves a project's team integration into a reader."""

    @abstractmethod
    async def reader(
        self, project_id: UUID, connection_id: str, *, acting_for: str
    ) -> IssueUpdateReader:
        """A reader over the project's team connection ``connection_id``.

        Every read through it is recorded as a use of the team connection for ``acting_for``.
        Raises ``ImportConnectionError`` when the connection is not one of the project's team
        integrations or cannot be used.
        """


class MemorySourceGateway(ABC):
    """The memory gateway's route for a connector to write a source."""

    @abstractmethod
    async def write_source(self, source: SourceWrite) -> SourceAccepted:
        """Write one source. Raises ``SourceGatewayError`` unless the gateway accepted it."""


class CompileLauncher(ABC):
    """Starts the bundled compile workflow for a project's new sources."""

    @abstractmethod
    async def launch(
        self,
        *,
        project_id: UUID,
        tenant_id: str,
        owner_id: str,
        source_ids: Sequence[str],
    ) -> str:
        """Start the run as ``owner_id``; return its id. Raises ``CompileLaunchError``."""
