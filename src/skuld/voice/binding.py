"""What a voice call is bound to: the one seam between the channel and its subject.

The channel (``skuld.voice.channel``) owns the wire, the persona, minting and the call
record. Everything that depends on *what the call is about* sits behind
:class:`VoiceCallBinding`: what grounds the voice model (how to behave on this call and
the data it opens with) and the tools the call has. The binding is chosen when the
channel is composed; the channel never assumes that this broker runs the agent the call
talks about.

Two bindings exist. :class:`~skuld.voice.session_binding.SessionVoiceBinding` steers the one
session this broker runs. :class:`~skuld.voice.room_binding.RoomVoiceBinding` binds a call to
the room this broker hosts when it runs no agent (a team's root, a workflow run's root): its
tools speak to the room's agents, its grounding is the roster and the room's recent posts,
and it pushes what the agents post to the phone while the call is up, through the
:class:`CallOutlet` the channel attaches. An agent's spoken line goes out as a ``speak``
frame naming the agent and its voices, so the phone says it in that agent's own voice:
nothing assumes every line is spoken in the call's one persona voice, which is the call's
default, used to mint and to open it.

Whatever the binding sends on the person's behalf is the person's own words, authored as
the verified caller (the owner), never as the voice model's.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from niuu.domain.message_author import MessageAuthor


class UnknownToolError(LookupError):
    """No voice tool by that name."""


@dataclass(frozen=True)
class ToolOutcome:
    """What the voice model hears back."""

    content: str
    is_error: bool = False


class CallOutlet(ABC):
    """How a binding reaches the phones on the call (the channel implements it)."""

    @abstractmethod
    async def push(self, message_type: str, payload: dict[str, Any]) -> int:
        """Send one frame to every connected call; the number of phones it reached."""


class VoiceCallBinding(ABC):
    """The subject of a voice call: its grounding and its tools."""

    def attach(self, outlet: CallOutlet) -> None:
        """Take the outlet to the phones; a binding that pushes nothing ignores it."""
        del outlet

    async def call_started(self, author: MessageAuthor | None) -> None:
        """A person started a call (``session.start``); a binding with no presence ignores it."""
        del author

    async def call_ended(self, author: MessageAuthor | None) -> None:
        """That person's call ended (``session.end`` or the socket closed)."""
        del author

    @property
    @abstractmethod
    def subject(self) -> str:
        """What the call is about, as the preamble names it (e.g. the Forge session «x»)."""

    @abstractmethod
    def sources(self) -> list[str]:
        """Where the call's context comes from (``persona.get``'s ``sources``)."""

    @abstractmethod
    def grounding(self) -> str:
        """How to behave on this call and the data it opens with, as instructions text."""

    @property
    @abstractmethod
    def tool_names(self) -> list[str]:
        """The names of the call's tools."""

    @abstractmethod
    def tool_definitions(self) -> list[dict[str, Any]]:
        """The call's tools in the app's shape (``AgentServiceToolDefinitionPayload``)."""

    @abstractmethod
    async def call_tool(
        self, name: str, args: dict[str, Any], *, author: MessageAuthor | None, call_id: str
    ) -> ToolOutcome:
        """Run one tool; raises :class:`UnknownToolError` for a name it does not have."""
