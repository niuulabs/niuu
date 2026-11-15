"""How the voice channel reaches the one session it is bound to.

The channel never touches the broker directly: it reads the session's state, sends a
message into it and writes the call record through this port, which the broker
implements (:class:`skuld.voice.broker_session.BrokerVoiceSession`). Tests drive the
channel with a fake.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from niuu.domain.message_author import MessageAuthor


class SendStatus(StrEnum):
    """What became of a message the call sent into the session."""

    #: The agent has taken it.
    DELIVERED = "delivered"
    #: Accepted and recorded; the agent is busy and takes it next.
    QUEUED = "queued"
    #: Not sent; ``reason`` says why.
    FAILED = "failed"


@dataclass(frozen=True)
class SendOutcome:
    status: SendStatus
    reason: str = ""
    #: The session has open questions for the person; a steer may wait on them.
    pending_questions: int = 0


@dataclass(frozen=True)
class SessionTurn:
    """One finished turn of the session's conversation, as the call reads it."""

    role: str
    text: str
    created_at: str = ""
    #: Who said a human turn (empty for the agent and for unattributed turns).
    author: str = ""


@dataclass(frozen=True)
class SessionVoiceStatus:
    """The session right now."""

    session_id: str
    name: str
    model: str
    #: The harness the session runs (claude, codex, …).
    harness: str
    #: The broker's raw activity state (idle, active, tool_executing, awaiting_input, …).
    activity_state: str
    #: Whether that state is the running bucket.
    busy: bool
    #: Epoch seconds the session has been in its coarse state, when known.
    state_since: float | None = None
    #: The questions the session is asking the person right now.
    questions: tuple[str, ...] = ()
    #: What the session is about, when it has a description.
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class VoiceSessionPort(ABC):
    """The session a voice call is bound to."""

    @abstractmethod
    def status(self) -> SessionVoiceStatus:
        """The session's state now."""

    @abstractmethod
    def turns(self) -> list[SessionTurn]:
        """The session's finished turns, oldest first."""

    @abstractmethod
    async def send(self, text: str, *, author: MessageAuthor | None, call_id: str) -> SendOutcome:
        """Send *text* into the session as the person on the call; never raises."""

    @abstractmethod
    def record(self, frame: dict[str, Any]) -> None:
        """Append one call-record frame to the session's durable event log."""
