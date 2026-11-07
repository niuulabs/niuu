"""Channel port — interface for emitting agent events to an output surface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.mesh.events import MeshEvent


class ChannelPort(ABC):
    """Abstract interface for an output channel (CLI, web socket, etc.)."""

    @abstractmethod
    async def emit(self, event: MeshEvent) -> None:
        """Emit a Ravn event to the output surface."""
        raise NotImplementedError
