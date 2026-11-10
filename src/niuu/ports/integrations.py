"""Port interface for integration connection persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from niuu.domain.models import IntegrationConnection, IntegrationType, IntegrationUsage


class IntegrationRepository(ABC):
    """Port for integration connection persistence."""

    @abstractmethod
    async def list_connections(
        self,
        owner_id: str,
        integration_type: IntegrationType | None = None,
    ) -> list[IntegrationConnection]:
        """List connections for a user, optionally filtered by type."""

    @abstractmethod
    async def list_connections_global(
        self,
        integration_type: IntegrationType | None = None,
        *,
        slug: str | None = None,
        enabled_only: bool = False,
    ) -> list[IntegrationConnection]:
        """List connections across all owners with optional filters."""

    @abstractmethod
    async def get_connection(self, connection_id: str) -> IntegrationConnection | None:
        """Get a single connection by ID."""

    @abstractmethod
    async def save_connection(
        self,
        connection: IntegrationConnection,
    ) -> IntegrationConnection:
        """Create or update a connection."""

    @abstractmethod
    async def delete_connection(self, connection_id: str) -> None:
        """Delete a connection by ID."""


class IntegrationUsageLog(ABC):
    """Record of what a team connection was used for, and for whom."""

    @abstractmethod
    async def record(self, usage: IntegrationUsage) -> None:
        """Store one use. Raises when it cannot: an unrecorded use is not allowed."""

    @abstractmethod
    async def list_for_project(
        self, project_id: str, *, limit: int, before: datetime | None = None
    ) -> list[IntegrationUsage]:
        """The project's uses, newest first, ``limit`` per page, older than ``before``."""
