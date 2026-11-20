"""Persistence port for question state (LEX-1932)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from volundr.domain.notifications import NotificationScope
from volundr.domain.questions import QuestionAnswer, QuestionEvent, QuestionRecord, QuestionState


class QuestionRepository(ABC):
    """Question states and their ordered change events, written together."""

    @abstractmethod
    async def get(self, notification_id: UUID) -> QuestionRecord | None:
        """The question's state, or ``None`` while it is open."""

    @abstractmethod
    async def claim_answer(
        self,
        *,
        notification_id: UUID,
        owner_id: str,
        tenant_id: str | None,
        session_id: UUID | None,
        answer: QuestionAnswer,
    ) -> tuple[QuestionRecord, bool]:
        """Record ``answer`` if the question is still open, atomically.

        Returns the stored record and whether this call recorded it (``False``: an
        earlier answer or close won, and the record is that one). Recording also
        appends an ``answered`` event in the same transaction.
        """

    @abstractmethod
    async def close(
        self,
        *,
        notification_id: UUID,
        owner_id: str,
        tenant_id: str | None,
        session_id: UUID | None,
        state: QuestionState,
        at: datetime,
        closed_by_notification_id: UUID | None,
    ) -> QuestionRecord | None:
        """Close an open or answered question as ``state`` (resolved or withdrawn).

        Returns the new record, or ``None`` when it was already closed (nothing changes).
        A close appends an event in the same transaction.
        """

    @abstractmethod
    async def events_after(
        self, scope: NotificationScope, *, after: int, limit: int
    ) -> list[QuestionEvent]:
        """The events ``scope`` may see with ``seq > after``, oldest first."""

    @abstractmethod
    async def head_seq(self, scope: NotificationScope) -> int:
        """The newest event ``scope`` may see (0 when none)."""
