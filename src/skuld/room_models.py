"""Room data models for multi-agent chat (Althing).

Defines participant identity and room state used in Phase 1 of the
multi-participant room chat feature. All fields are optional in the
single-agent path — these models only appear when room mode is active.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParticipantMeta:
    """Identity and display metadata for a single room participant.

    Attributes:
        peer_id: Stable identifier for this participant (e.g. user sub or agent ID).
        persona: Display name shown in the chat UI.
        color: Accent token name from the design system (e.g. "amber", "cyan").
        participant_type: "human" for human users, "ravn" for mesh peers,
            "skuld" for the local broker/orchestrator.
        gateway_url: WebSocket gateway URL for agent participants; None for humans.
        subscribes_to: Event types this participant listens for.
        emits: Event types this participant can publish.
        tools: Tool names available to this participant.
    """

    peer_id: str
    persona: str
    color: str
    participant_type: str  # "human" | "ravn" | "skuld"
    display_name: str = ""
    gateway_url: str | None = None
    subscribes_to: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    status: str = "idle"


@dataclass(frozen=True)
class RoomState:
    """Snapshot of all participants currently in a multi-agent room.

    Attributes:
        participants: Mapping from peer_id to ParticipantMeta.
    """

    participants: dict[str, ParticipantMeta] = field(default_factory=dict)
