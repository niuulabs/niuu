"""What a room host hands to Forge for a member session, and the error when it cannot.

Kept apart from :mod:`skuld.collaboration_adapter` so the broker can raise and type these
without importing the room implementation, which an ordinary session never loads.
"""

from __future__ import annotations

from dataclasses import dataclass

from niuu.domain.peer_envelope import RelayedSpeech


class RoomDeliveryError(RuntimeError):
    """A room delivery to a member session failed. It is raised, never logged and dropped."""


@dataclass(frozen=True)
class RoomDelivery:
    """One room message for a member session: what is said and who vouches for it.

    The hosting session is the caller that sends it, so Forge stamps it ``relayed_by``.
    ``speech`` names the participant who spoke and the room, and where an answer goes.
    ``request_id`` is fixed for the delivery so a retry is never delivered twice.
    """

    content: str
    speech: RelayedSpeech
    request_id: str


#: The mesh RPC a member session posts in a room with, to the session that hosts the room.
ROOM_MESSAGE_RPC_TYPE = "room_message"
