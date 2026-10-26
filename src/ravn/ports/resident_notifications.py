"""Port for a resident's notification outbox: what its owner should hear about.

The resident writes here when it starts waiting for its owner, when that wait is
over, and when its agent notifies on purpose. Forge drains the outbox over the
Ravn OpenClaw shim and records each entry in its notification feed (see
:mod:`niuu.domain.resident_notifications`). The outbox is durable, so nothing is
lost while Forge is unreachable; Forge acknowledges what it recorded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from niuu.domain.notifications import NotificationDraft
from niuu.domain.resident_notifications import AttentionInputKind, ResidentNotificationEntry


class ResidentNotificationOutboxPort(ABC):
    """A resident's durable, ordered notification outbox.

    The composition root that builds an outbox owns it and calls :meth:`close`
    when the resident shuts down.
    """

    @abstractmethod
    def close(self) -> None:
        """Release what the outbox holds open (its database connection).

        Idempotent. Nothing may be written after it.
        """

    @abstractmethod
    async def attention(
        self,
        *,
        request_id: str,
        group: str,
        input_kind: AttentionInputKind,
        prompt: str,
        channel: str | None = None,
    ) -> bool:
        """Record that the resident waits for its owner on ``request_id``.

        ``group`` is what a later :meth:`resolve` settles (e.g. the case the
        question belongs to). A request id already recorded, open or settled, is
        not raised again; returns whether this call recorded it.
        """

    @abstractmethod
    async def resolve(self, *, group: str) -> int:
        """Settle every open request in ``group``; returns how many it settled."""

    @abstractmethod
    async def notify(
        self,
        *,
        draft: NotificationDraft,
        idempotency_key: str,
        channel: str | None = None,
    ) -> ResidentNotificationEntry:
        """Queue a notification the agent sends on purpose."""

    @abstractmethod
    async def pending(self, *, limit: int) -> list[ResidentNotificationEntry]:
        """The oldest entries Forge has not acknowledged yet, in write order."""

    @abstractmethod
    async def acknowledge(self, *, through_seq: int) -> int:
        """Drop every entry up to ``through_seq``; returns how many were dropped."""

    @abstractmethod
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after each new entry; returns the unsubscribe callable."""
