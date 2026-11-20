"""Question state (LEX-1932): one answer per question, shared by every device.

A question is a notice with ``question: true`` (agent ``notify``, direct submit) or the
system ``attention`` item a runtime question raises (``input_request``). It is open
until its node records a state for it:

- ``answered``: the first answer, from one device, delivered to the asking session.
  The question's notification is also resolved ("Answered on <device>"), so the feed
  and older app builds agree. A second answer is refused with the first.
- ``resolved`` / ``withdrawn``: the asking session closed it with ``about_question``.

Every change is also an event, in order, so the Guild push gateway can tell each of
the owner's iOS devices with a silent push. The wire shapes below are a contract with
the app (LEX-1932, agreed with ios-omi 2026-11-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

#: The answering device, as the app names it: an opaque per-install id and a label.
MAX_DEVICE_ID_CHARS = 64
MAX_DEVICE_LABEL_CHARS = 40
#: The resolution an answered question's notification gets (the feed's ``resolution``).
ANSWERED_RESOLUTION = "Answered on {label}"
#: The resolution of a runtime question once its session waits for nothing any more.
SETTLED_IN_SESSION_RESOLUTION = "No longer waiting in the session"


class QuestionState(StrEnum):
    ANSWERED = "answered"
    RESOLVED = "resolved"
    WITHDRAWN = "withdrawn"

    @property
    def closed(self) -> bool:
        return self is not QuestionState.ANSWERED


@dataclass(frozen=True)
class AnsweredBy:
    """The device an answer came from."""

    device_id: str
    device_label: str

    def wire(self) -> dict[str, str]:
        return {"device_id": self.device_id, "device_label": self.device_label}


@dataclass(frozen=True)
class QuestionAnswer:
    """The first answer: what was sent to the asking session, and from where.

    ``request_id`` is the message's id at the session; repeating it repeats the same
    answer (a retry), any other id is a second answer. ``device`` is ``None`` when the
    runtime reported the answer (its question card was answered in the session).
    """

    request_id: str | None
    device: AnsweredBy | None
    content: str | None
    answers: list[Any] | None
    at: datetime


@dataclass(frozen=True)
class QuestionRecord:
    """A question's recorded state on the node that holds it."""

    notification_id: UUID
    owner_id: str
    tenant_id: str | None
    session_id: UUID | None
    state: QuestionState
    answer: QuestionAnswer | None = None
    closed_at: datetime | None = None
    closed_by_notification_id: UUID | None = None

    @property
    def at(self) -> datetime:
        """When it reached its current state."""
        if self.closed_at is not None:
            return self.closed_at
        if self.answer is None:
            raise ValueError(f"Question {self.notification_id} has neither an answer nor a close")
        return self.answer.at

    def wire(self, ref: str) -> dict[str, Any]:
        """The ``question`` object of an answer response and a 409 (LEX-1932)."""
        answer = self.answer
        return {
            "ref": ref,
            "notification_id": str(self.notification_id),
            "state": self.state.value,
            "answered_by": answer.device.wire() if answer and answer.device else None,
            "at": self.at.isoformat(),
            "answer": ({"content": answer.content, "answers": answer.answers} if answer else None),
        }


@dataclass(frozen=True)
class QuestionEvent:
    """One state change, in the node's order (``seq``)."""

    seq: int
    notification_id: UUID
    owner_id: str
    tenant_id: str | None
    state: QuestionState
    answered_by: AnsweredBy | None
    at: datetime

    def wire(self, ref: str) -> dict[str, Any]:
        """A ``GET /notifications/questions/events`` item and the push's ``question``."""
        return {
            "seq": self.seq,
            "ref": ref,
            "notification_id": str(self.notification_id),
            "owner_id": self.owner_id,
            "state": self.state.value,
            "answered_by": self.answered_by.wire() if self.answered_by else None,
            "at": self.at.isoformat(),
        }


class QuestionError(Exception):
    """A question cannot take this answer or change."""


class NotAQuestionError(QuestionError):
    """The notification is not a question (no ``question: true``, no input request)."""


class QuestionAnsweredError(QuestionError):
    """A second answer: the question already has a different one."""

    def __init__(self, record: QuestionRecord) -> None:
        super().__init__(f"Question {record.notification_id} is already answered")
        self.record = record


class QuestionClosedError(QuestionError):
    """The question is resolved or withdrawn (or its notice was resolved by hand)."""

    def __init__(self, record: QuestionRecord | None, resolution: str | None = None) -> None:
        super().__init__("This question is closed" + (f": {resolution}" if resolution else ""))
        self.record = record
        self.resolution = resolution
