"""A message one agent addresses to another, with its sender stamped by the platform.

Every message an agent sends to another agent travels as a :class:`PeerMessage`:

* a Forge session message (the session MCP ``send_message`` tool);
* a room message delivered to a member session;
* a direct message on the mesh.

The sender is stamped by whoever can verify it. Forge stamps it from the caller's
session credential, and Skuld stamps it from its own session identity. It is never
taken from the message text, and an agent cannot set it. A room host that relays a
participant's message stamps itself as ``relayed_by`` and names the participant as
the sender, so the recipient can see who vouched for what.

The recipient's Skuld turns the envelope into the text the harness reads
(:func:`render_for_harness`), so every harness sees the same header: who sent it,
when, and how to answer. The answer is an explicit tool call (``reply`` in the
session MCP), never text parsed out of the recipient's turn.

People are not peers. A person's chat message is not a ``PeerMessage``, and it
still interrupts a running turn. A ``PeerMessage`` waits for the turn boundary
unless it is ``urgent``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

PEER_MESSAGE_VERSION = 1


class PeerMessageError(ValueError):
    """A peer message that is malformed, or a lookup of one that does not exist."""


class PeerKind(StrEnum):
    """What kind of party sends or receives a peer message."""

    SESSION = "session"
    MESH_PEER = "mesh_peer"
    PERSON = "person"


class ReplyKind(StrEnum):
    """Where an answer to a peer message goes."""

    SESSION = "session"
    MESH = "mesh"
    ROOM = "room"


@dataclass(frozen=True)
class PeerAddress:
    """One party to a message: a Forge session, a mesh peer, or a person.

    ``id`` is the stable identity (a session id, a mesh peer id, a user id).
    ``name`` is what agents address it by and what the recipient reads. ``node``
    is the Forge instance it lives on, when that matters for routing.
    """

    kind: PeerKind
    id: str
    name: str = ""
    node: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise PeerMessageError(f"a {self.kind.value} address needs an id")

    def label(self) -> str:
        """``name (kind id on node)``, the way a recipient reads the party."""
        where = f" on {self.node}" if self.node else ""
        identity = f"{self.kind.value} {self.id}{where}"
        return f"{self.name} ({identity})" if self.name else identity

    def to_wire(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "id": self.id, "name": self.name, "node": self.node}

    @classmethod
    def from_wire(cls, raw: Any, *, field_name: str) -> PeerAddress:
        if not isinstance(raw, Mapping):
            raise PeerMessageError(f"{field_name} must be an object")
        return cls(
            kind=_enum(PeerKind, raw.get("kind"), f"{field_name}.kind"),
            id=_text(raw.get("id"), f"{field_name}.id", required=True),
            name=_text(raw.get("name"), f"{field_name}.name"),
            node=_text(raw.get("node"), f"{field_name}.node"),
        )


@dataclass(frozen=True)
class ReplyRoute:
    """How the recipient answers: to a session, a mesh peer, or a room."""

    kind: ReplyKind
    target: str
    node: str = ""

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise PeerMessageError(f"a {self.kind.value} reply route needs a target")

    def to_wire(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "target": self.target, "node": self.node}

    @classmethod
    def from_wire(cls, raw: Any) -> ReplyRoute:
        if not isinstance(raw, Mapping):
            raise PeerMessageError("reply must be an object or null")
        return cls(
            kind=_enum(ReplyKind, raw.get("kind"), "reply.kind"),
            target=_text(raw.get("target"), "reply.target", required=True),
            node=_text(raw.get("node"), "reply.node"),
        )


@dataclass(frozen=True)
class PeerMessage:
    """One message from one party to another, as the platform delivers it.

    ``reply`` is ``None`` when the sender takes no answer (a notice). ``in_reply_to``
    names the message this one answers. ``room_id`` is set when the message was
    said in a room. ``urgent`` lets it interrupt a running turn instead of waiting
    for the turn boundary.
    """

    message_id: str
    sender: PeerAddress
    recipient: PeerAddress
    sent_at: datetime
    content: str
    reply: ReplyRoute | None = None
    in_reply_to: str = ""
    room_id: str = ""
    relayed_by: PeerAddress | None = None
    urgent: bool = False

    def __post_init__(self) -> None:
        if not self.message_id.strip():
            raise PeerMessageError("a peer message needs a message_id")
        if not self.content.strip():
            raise PeerMessageError("a peer message needs content")
        if self.sent_at.tzinfo is None:
            raise PeerMessageError("sent_at must be timezone-aware")

    def to_wire(self) -> dict[str, Any]:
        return {
            "version": PEER_MESSAGE_VERSION,
            "message_id": self.message_id,
            "sender": self.sender.to_wire(),
            "recipient": self.recipient.to_wire(),
            "sent_at": self.sent_at.astimezone(UTC).isoformat(),
            "content": self.content,
            "reply": self.reply.to_wire() if self.reply else None,
            "in_reply_to": self.in_reply_to,
            "room_id": self.room_id,
            "relayed_by": self.relayed_by.to_wire() if self.relayed_by else None,
            "urgent": self.urgent,
        }

    @classmethod
    def from_wire(cls, raw: Any) -> PeerMessage:
        """Parse a wire envelope strictly; anything malformed raises, nothing is guessed."""
        if not isinstance(raw, Mapping):
            raise PeerMessageError("a peer message must be an object")
        version = raw.get("version")
        if version != PEER_MESSAGE_VERSION:
            raise PeerMessageError(
                f"unsupported peer message version {version!r}; this build reads "
                f"version {PEER_MESSAGE_VERSION}"
            )
        urgent = raw.get("urgent", False)
        if not isinstance(urgent, bool):
            raise PeerMessageError("urgent must be true or false")
        reply = raw.get("reply")
        relayed_by = raw.get("relayed_by")
        return cls(
            message_id=_text(raw.get("message_id"), "message_id", required=True),
            sender=PeerAddress.from_wire(raw.get("sender"), field_name="sender"),
            recipient=PeerAddress.from_wire(raw.get("recipient"), field_name="recipient"),
            sent_at=_timestamp(raw.get("sent_at")),
            content=_text(raw.get("content"), "content", required=True),
            reply=None if reply is None else ReplyRoute.from_wire(reply),
            in_reply_to=_text(raw.get("in_reply_to"), "in_reply_to"),
            room_id=_text(raw.get("room_id"), "room_id"),
            relayed_by=None
            if relayed_by is None
            else PeerAddress.from_wire(relayed_by, field_name="relayed_by"),
            urgent=urgent,
        )


#: The session MCP tool a recipient answers with; :func:`render_for_harness` names it.
REPLY_TOOL = "reply"


def render_for_harness(message: PeerMessage) -> str:
    """The text a recipient harness reads: a header the platform wrote, then the content.

    Every Skuld transport delivers this same text, so every harness sees who sent the
    message, when, and how to answer it.
    """
    lines = [
        f"[Message {message.message_id} from {message.sender.label()}, "
        f"sent {message.sent_at.astimezone(UTC).isoformat()}]"
    ]
    if message.room_id:
        lines.append(f"[Said in room {message.room_id}]")
    if message.relayed_by is not None:
        lines.append(f"[Relayed by {message.relayed_by.label()}]")
    if message.in_reply_to:
        lines.append(f"[In reply to your message {message.in_reply_to}]")
    lines.append("")
    lines.append(message.content)
    lines.append("")
    if message.reply is None:
        lines.append("[The sender takes no reply to this message.]")
    else:
        lines.append(
            f'[Answer with the `{REPLY_TOOL}` tool, message_id "{message.message_id}". '
            "Text you write in your turn does not reach the sender.]"
        )
    return "\n".join(lines)


def _text(value: Any, field_name: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise PeerMessageError(f"{field_name} is required")
        return ""
    if not isinstance(value, str):
        raise PeerMessageError(f"{field_name} must be a string")
    if required and not value.strip():
        raise PeerMessageError(f"{field_name} is required")
    return value


def _enum[E: StrEnum](enum: type[E], value: Any, field_name: str) -> E:
    try:
        return enum(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum)
        raise PeerMessageError(f"{field_name} must be one of {allowed}; got {value!r}") from exc


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise PeerMessageError("sent_at must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PeerMessageError(f"sent_at is not ISO 8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise PeerMessageError("sent_at must carry a timezone")
    return parsed
