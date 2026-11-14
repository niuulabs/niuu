"""What a voice call is bound to: the one seam between the channel and its subject.

The channel (``skuld.voice.channel``) owns the wire, the persona, minting and the call
record. Everything that depends on *what the call is about* sits behind
:class:`VoiceCallBinding`: what grounds the voice model (how to behave on this call and
the data it opens with) and the tools the call has. The binding is chosen when the
channel is composed; the channel never assumes that this broker runs the agent the call
talks about.

Today there is one binding, :class:`~skuld.voice.session_binding.SessionVoiceBinding`: the
call steers the one session this broker runs. A room binding (a team call on a unit root
that hosts a routed room and runs no agent) is a second implementation: its tools are the
roster and saying something to one member or to everyone, its grounding the room feed. It
will also have the phone speak a member's line in that member's own voice, so nothing
here or in the channel assumes every line is spoken in the call's one persona voice: the
persona voice is the call's default, used to mint and to open it.

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


class VoiceCallBinding(ABC):
    """The subject of a voice call: its grounding and its tools."""

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
