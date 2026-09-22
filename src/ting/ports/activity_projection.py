"""Port for projecting a session's activity onto durable state."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ting.ports.volundr import ActivityEvent


class ActivityProjector(ABC):
    """Observe one session activity event and record what it implies."""

    @abstractmethod
    async def handle_activity(self, event: ActivityEvent, owner_id: str) -> bool:
        """Return whether the event was projected."""
