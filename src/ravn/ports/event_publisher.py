"""EventPublisherPort — out-of-band event publisher for drive-loop lifecycle events."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.events import RavnEvent


class EventPublisherPort(ABC):
    """Out-of-band event publisher for system-level events.

    Used by the drive loop to publish lifecycle events (task started,
    task complete, heartbeat, surface escalation) that are not tied to
    a specific agent session turn.

    Distinct from ChannelPort, which is per-session and per-turn.
    """

    @abstractmethod
    async def publish(self, event: RavnEvent) -> None:
        """Publish an event. Never raises — implementations must absorb errors."""
        raise NotImplementedError

    async def close(self) -> None:
        """Optional cleanup. Called on daemon shutdown."""
