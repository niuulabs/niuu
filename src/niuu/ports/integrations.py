"""Port interface for integration connection persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.models import IntegrationConnection, IntegrationType


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
