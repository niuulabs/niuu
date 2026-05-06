"""Port for flock flow configuration providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from tyr.domain.flock_flow import FlockFlowConfig


class FlockFlowProvider(ABC):
    """Abstract interface for flock flow CRUD operations."""

    @abstractmethod
    def get(self, name: str) -> FlockFlowConfig | None:
        """Return the flow with the given name, or None."""

    @abstractmethod
    def list(self) -> list[FlockFlowConfig]:
        """Return all known flows."""

    @abstractmethod
    def save(self, flow: FlockFlowConfig) -> None:
        """Create or update a flow."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Delete a flow by name. Returns True if it existed."""
