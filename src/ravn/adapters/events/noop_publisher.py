"""No-op event publisher — silently discards all events."""

from __future__ import annotations

from niuu.mesh.events import MeshEvent
from ravn.ports.event_publisher import EventPublisherPort


class NoOpEventPublisher(EventPublisherPort):
    """Silently discards all events. Default when Sleipnir is not configured."""

    async def publish(self, event: MeshEvent) -> None:
        pass
