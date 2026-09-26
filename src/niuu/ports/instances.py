"""Port interface for registered runtime instances."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from niuu.domain.models import InstanceHealthStatus, InstanceKind, RegisteredInstance


class InstanceRepository(ABC):
    """Persistence port for registered runtime instances."""

    @abstractmethod
    async def list_instances(self, kind: InstanceKind | None = None) -> list[RegisteredInstance]:
        """List all registered instances, optionally filtered by kind."""

    @abstractmethod
    async def get_instance(self, instance_id: str) -> RegisteredInstance | None:
        """Get a registered instance by ID."""

    @abstractmethod
    async def list_for_node(self, node_id: str) -> list[RegisteredInstance]:
        """List instances owned by *node_id* (the real ownership column)."""

    @abstractmethod
    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        """Create or update a registered instance."""

    @abstractmethod
    async def delete_instance(self, instance_id: str) -> None:
        """Delete a registered instance by ID."""

    @abstractmethod
    async def record_health(
        self,
        instance_id: str,
        *,
        health: InstanceHealthStatus,
        last_seen_at: datetime | None,
        last_checked_at: datetime,
        last_error: str | None,
    ) -> None:
        """Record the outcome of a reachability probe for one instance.

        ``last_seen_at`` is the last time the instance answered a probe
        successfully — callers pass the previous value unchanged on a failed
        probe rather than clearing it, so the UI can still say how long the
        node has been gone. ``last_checked_at`` is the last time a probe was
        attempted at all, success or not, and always advances.
        """
