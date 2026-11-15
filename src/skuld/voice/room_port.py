"""How a voice call reaches the room it is bound to.

A room host is a broker that runs no agent and hosts a routed room: a Forge team's root, or
a workflow run's root. The room binding reads its roster and posts, says something to one
agent or to every agent as the person on the call, and hears each new post, all through
this port, which the broker implements (:class:`skuld.voice.broker_room.BrokerVoiceRoom`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from niuu.domain.message_author import MessageAuthor
from skuld.voice.session_port import SendOutcome


class RoomKind(StrEnum):
    #: A Forge team's room: a person may speak to every agent at once.
    TEAM = "team"
    #: A workflow run's room: steps and gates; a person speaks to one agent at a time.
    RUN = "run"


class PostKind(StrEnum):
    """What a room post is, as the call treats it."""

    #: An agent's reply.
    ANSWER = "answer"
    #: An agent asks the person something (it waits on them).
    QUESTION = "question"
    #: An agent's work failed.
    ERROR = "error"
    #: The host's or an agent's notice (a step, a gate, a hand-off): context, never spoken.
    NOTICE = "notice"
    #: A person wrote in the room.
    PERSON = "person"


#: Kinds the call speaks on its own initiative (under the phone's quiet gate and rate rules).
SPEAKABLE_KINDS = frozenset({PostKind.ANSWER, PostKind.QUESTION, PostKind.ERROR})


@dataclass(frozen=True)
class RoomMember:
    """One agent of the room."""

    name: str
    persona: str = ""
    lead: bool = False
    #: Whether the agent is seated in the room right now.
    present: bool = False
    #: Its voice per persona voice key (grok-voice, openai-realtime); empty: the host's.
    voices: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RoomPost:
    """One post in the room."""

    post_id: str
    speaker: str
    kind: PostKind
    text: str
    #: The agent's persona (empty for a person or the host).
    persona: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class RoomVoiceStatus:
    """The room right now."""

    room_id: str
    name: str
    kind: RoomKind
    members: tuple[RoomMember, ...] = ()
    #: The people in the room right now (by name).
    people_online: tuple[str, ...] = ()

    @property
    def speaks_to_everyone(self) -> bool:
        """Whether one message can go to every agent (a team's room delivers it to each)."""
        return self.kind is RoomKind.TEAM


class VoiceRoomPort(ABC):
    """The room a voice call is bound to."""

    @abstractmethod
    def status(self) -> RoomVoiceStatus:
        """The room's roster now."""

    @abstractmethod
    def posts(self) -> list[RoomPost]:
        """The room's posts, oldest first."""

    @abstractmethod
    async def say_to(
        self, member: str, text: str, *, author: MessageAuthor | None, model_note: str
    ) -> SendOutcome:
        """Say *text* to one agent as the person; never raises."""

    @abstractmethod
    async def say_to_everyone(
        self, text: str, *, author: MessageAuthor | None, model_note: str
    ) -> SendOutcome:
        """Say *text* to every agent as the person; never raises."""

    @abstractmethod
    def listen(self, listener: Callable[[RoomPost], None] | None) -> None:
        """Have *listener* told of every new post (``None`` stops it)."""

    @abstractmethod
    def record(self, frame: dict) -> None:
        """Append one call-record frame to the room host's durable event log."""
