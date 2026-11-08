"""Building the :class:`~niuu.domain.peer_message.PeerMessage` a platform host stamps.

The envelope contract lives in :mod:`niuu.domain.peer_message`. This module is how the
parties that can *verify* a sender build one: Forge from a session credential, and Skuld
from its own session identity. Nothing here reads a sender out of message text.

A message id is derived from the sender, the recipient and the caller's ``request_id``, so a
retry of the same request carries the same id and the recipient's inbox deduplicates it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from niuu.domain.peer_message import (
    PeerAddress,
    PeerKind,
    PeerMessage,
    PeerMessageError,
    ReplyKind,
    ReplyRoute,
)

#: Namespace of derived message ids (a protocol constant, not a tunable).
PEER_MESSAGE_ID_NAMESPACE = uuid.UUID("0b0f6c0e-5a52-4c5b-9a43-6e1b8f2f7a10")

#: The body keys a caller must never set: the platform stamps all of them.
STAMPED_BODY_KEYS = frozenset({"sender", "recipient", "relayed_by", "sent_at", "message_id"})


def derive_message_id(sender: PeerAddress, recipient: PeerAddress, request_id: str) -> str:
    """The same id for the same ``(sender, recipient, request_id)``, so a retry deduplicates."""
    name = f"{sender.kind.value}:{sender.id}\n{recipient.kind.value}:{recipient.id}\n{request_id}"
    return str(uuid.uuid5(PEER_MESSAGE_ID_NAMESPACE, name))


def session_reply_route(sender: PeerAddress) -> ReplyRoute:
    """A reply route back to a session sender."""
    return ReplyRoute(kind=ReplyKind.SESSION, target=sender.id, node=sender.node)


def build_session_message(
    *,
    sender: PeerAddress,
    recipient: PeerAddress,
    content: str,
    request_id: str,
    in_reply_to: str = "",
    urgent: bool = False,
    now: datetime | None = None,
) -> PeerMessage:
    """A message from a session the platform verified, answerable by a session reply."""
    return PeerMessage(
        message_id=derive_message_id(sender, recipient, request_id),
        sender=sender,
        recipient=recipient,
        sent_at=now or datetime.now(UTC),
        content=content,
        reply=session_reply_route(sender),
        in_reply_to=in_reply_to,
        urgent=urgent,
    )


class RelayedSpeech:
    """What a room host asserts about a message it relays: who spoke, in which room.

    The host is the verified caller; it is stamped ``relayed_by`` by whoever builds the
    envelope. ``speaker`` and ``room_id`` are the host's claim, which is why the recipient
    is shown the host next to them.
    """

    __slots__ = ("reply", "room_id", "speaker")

    def __init__(self, *, speaker: PeerAddress, room_id: str, reply: ReplyRoute | None) -> None:
        if not room_id.strip():
            raise PeerMessageError("a relayed room message needs a room_id")
        self.speaker = speaker
        self.room_id = room_id
        self.reply = reply

    @classmethod
    def from_body(cls, raw: Any) -> RelayedSpeech:
        """Parse a request body's ``peer`` block; anything malformed raises."""
        if not isinstance(raw, Mapping):
            raise PeerMessageError("peer must be an object")
        unknown = set(raw) - {"speaker", "room_id", "reply"}
        if unknown:
            raise PeerMessageError(f"peer has unknown keys: {', '.join(sorted(unknown))}")
        reply = raw.get("reply")
        room_id = raw.get("room_id")
        if not isinstance(room_id, str):
            raise PeerMessageError("peer.room_id must be a string")
        return cls(
            speaker=PeerAddress.from_wire(raw.get("speaker"), field_name="peer.speaker"),
            room_id=room_id,
            reply=None if reply is None else ReplyRoute.from_wire(reply),
        )

    def to_body(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker.to_wire(),
            "room_id": self.room_id,
            "reply": self.reply.to_wire() if self.reply else None,
        }


def build_relayed_message(
    *,
    relayed_by: PeerAddress,
    recipient: PeerAddress,
    speech: RelayedSpeech,
    content: str,
    request_id: str,
    in_reply_to: str = "",
    urgent: bool = False,
    now: datetime | None = None,
) -> PeerMessage:
    """A room participant's message that a verified host session relays to a member session."""
    if relayed_by.kind is not PeerKind.SESSION:
        raise PeerMessageError("a relayed message is relayed by a session")
    return PeerMessage(
        message_id=derive_message_id(speech.speaker, recipient, request_id),
        sender=speech.speaker,
        recipient=recipient,
        sent_at=now or datetime.now(UTC),
        content=content,
        reply=speech.reply,
        in_reply_to=in_reply_to,
        room_id=speech.room_id,
        relayed_by=relayed_by,
        urgent=urgent,
    )
