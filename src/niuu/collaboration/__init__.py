"""Shared collaboration contracts used by Ravn and Skuld.

This package contains transport-neutral room state and messaging primitives.
It deliberately has no dependency on either runtime package.
"""

from niuu.collaboration.models import Participant, RoomMessage, RoomState
from niuu.collaboration.room import CollaborationEvent, CollaborationRoom

__all__ = [
    "CollaborationEvent",
    "CollaborationRoom",
    "Participant",
    "RoomMessage",
    "RoomState",
]
