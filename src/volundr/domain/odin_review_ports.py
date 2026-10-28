"""Ports for following the ODIN review queue from Forge."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from volundr.domain.odin_review import OwnerRelayState, PendingQuestions


class OdinReviewSourceError(Exception):
    """The review queue could not be read; the message names the remedy."""


class OdinReviewSource(ABC):
    """Reads resident questions from the ODIN review queue.

    Every failure raises :class:`OdinReviewSourceError`; an empty result always means
    the queue holds no pending question.
    """

    @abstractmethod
    async def pending_questions(self) -> PendingQuestions:
        """Every pending ``answer_operator_question`` item."""

    @abstractmethod
    async def is_pending(self, review_id: str) -> bool:
        """Whether the item is still waiting for an answer.

        ``False`` for a settled item and for one the queue no longer knows.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release network clients. Safe to call more than once."""


class OdinReviewRelayStateStore(ABC):
    """Durable memory of what the relay already announced.

    Shared by every Forge replica, so a question is announced once and the backlog
    watermark is set once.
    """

    @abstractmethod
    async def begin_owner(self, owner_id: str, watermark_at: datetime) -> OwnerRelayState:
        """Start following the queue for ``owner_id`` and return their state.

        The first call stores ``watermark_at``; later calls (from any replica) return
        the stored one, never moving it.
        """

    @abstractmethod
    async def mark_digest_recorded(self, owner_id: str) -> None:
        """Remember that the owner's backlog digest has been recorded."""

    @abstractmethod
    async def recorded_among(self, owner_id: str, review_ids: set[str]) -> set[str]:
        """The given review ids that already have a notification for the owner."""

    @abstractmethod
    async def mark_recorded(self, owner_id: str, review_id: str) -> None:
        """Remember that the owner has a notification for the item (idempotent)."""

    @abstractmethod
    async def open_ids(self, owner_id: str) -> set[str]:
        """The owner's recorded items whose notification is not yet retired."""

    @abstractmethod
    async def mark_retired(self, owner_id: str, review_id: str) -> None:
        """Remember that the owner's notification for the item was retired."""
