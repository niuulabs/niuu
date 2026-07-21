"""Transport-neutral collaboration domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Participant:
    """Identity, presence, and declared capabilities of a room participant."""

    peer_id: str
    persona: str
    color: str
    participant_type: str
    display_name: str = ""
    gateway_url: str | None = None
    subscribes_to: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    status: str = "idle"
    environment_id: str = ""
    participant_kind: str = ""
    capabilities: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    wakefulness: str = "unknown"
    attention_state: str = "available"
    heartbeat_ttl_s: float = 90.0
    last_heartbeat_at: float | None = None
    authority_role: str = ""
    room_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoomMessage:
    """A transport-neutral message recorded in a collaboration room."""

    message_id: str
    room_id: str
    environment_id: str
    participant_id: str
    role: str
    content: str
    visibility: str = "public"
    thread_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_event(self) -> dict[str, Any]:
        """Return the established room wire representation."""
        return {
            "type": "room_message",
            "id": self.message_id,
            "roomId": self.room_id,
            "environmentId": self.environment_id,
            "participantId": self.participant_id,
            "role": self.role,
            "content": self.content,
            "visibility": self.visibility,
            "threadId": self.thread_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RoomState:
    """Snapshot of the participants currently known to a room."""

    participants: dict[str, Participant] = field(default_factory=dict)

    def to_event(self) -> dict[str, Any]:
        """Return the established room-state wire representation."""
        return {
            "type": "room_state",
            "participants": [asdict(participant) for participant in self.participants.values()],
        }


__all__ = ["Participant", "RoomMessage", "RoomState"]
