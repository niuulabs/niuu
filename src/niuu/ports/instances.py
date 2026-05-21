"""Port interface for registered runtime instances."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.models import InstanceKind, RegisteredInstance


class InstanceRepository(ABC):
    """Persistence port for registered runtime instances."""

    @abstractmethod
    async def list_instances(self, kind: InstanceKind | None = None) -> list[RegisteredInstance]:
        """List all registered instances, optionally filtered by kind."""

    @abstractmethod
    async def get_instance(self, instance_id: str) -> RegisteredInstance | None:
        """Get a registered instance by ID."""

    @abstractmethod
    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        """Create or update a registered instance."""

    @abstractmethod
    async def delete_instance(self, instance_id: str) -> None:
        """Delete a registered instance by ID."""
