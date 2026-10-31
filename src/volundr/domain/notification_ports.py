"""Ports for Forge notifications: the feed, reader watermarks, rules and the outbox."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from niuu.ports.notifications import NotificationSink
from volundr.domain.models import Session
from volundr.domain.notifications import (
    ClaimedDelivery,
    Notification,
    NotificationCandidate,
    NotificationDelivery,
    NotificationPage,
    NotificationQuery,
    NotificationRule,
    NotificationScope,
    ReadWatermark,
)


class NotificationRepository(ABC):
    """The durable notification feed and per-reader read watermarks."""

    @abstractmethod
    async def project(self, candidates: list[NotificationCandidate]) -> list[Notification]:
        """Record candidates idempotently and schedule their deliveries atomically.

        In ONE transaction: insert each candidate ``ON CONFLICT (dedupe_key) DO
        NOTHING``; for every row that is actually new, evaluate its owner's enabled
        rules (:func:`volundr.domain.notifications.rules_matching`) and insert a
        ``pending`` delivery per match. Seqs are allocated and committed in the same
        order, so a reader resuming ``after=<seq>`` never skips a late commit.
        Returns only the NEW rows, in seq order; a replayed candidate yields nothing.
        """

    @abstractmethod
    async def get(self, notification_id: UUID) -> Notification | None:
        """Return one notification by id."""

    @abstractmethod
    async def list_feed(
        self,
        scope: NotificationScope,
        query: NotificationQuery,
        *,
        read_through_seq: int,
    ) -> NotificationPage:
        """Return one page of the reader's feed.

        ``read_through_seq`` is the reader's watermark, used by ``query.unread``.
        Pages without ``after`` are newest first and set ``next_before`` when older
        rows remain. ``after`` pages are ascending.
        """

    @abstractmethod
    async def list_for_session(
        self, session_id: UUID, *, after: int, limit: int
    ) -> list[Notification]:
        """Return one session's notifications with ``seq > after``, ascending."""

    @abstractmethod
    async def feed_counts(
        self, scope: NotificationScope, *, read_through_seq: int
    ) -> tuple[int, int]:
        """Return ``(head_seq, unread_count)`` for the reader's whole scope."""

    @abstractmethod
    async def get_watermark(self, user_id: str) -> ReadWatermark:
        """Return the reader's watermark (zeroes when they never marked anything)."""

    @abstractmethod
    async def advance_watermark(
        self, user_id: str, *, read_through_seq: int, expected_revision: int
    ) -> ReadWatermark:
        """Compare-and-swap the watermark forward.

        The stored value becomes ``max(current, read_through_seq)`` and the revision
        increments. Raises ``NotificationReadStateConflictError`` when the stored
        revision is not ``expected_revision``.
        """

    @abstractmethod
    async def read_ids(self, user_id: str, notification_ids: list[UUID]) -> set[UUID]:
        """Return individual acknowledgements among this bounded page of IDs."""

    @abstractmethod
    async def mark_read(self, user_id: str, notification_id: UUID) -> None:
        """Idempotently acknowledge only this item; increment the reader revision atomically."""


class NotificationRuleRepository(ABC):
    """Owner-scoped delivery rules."""

    @abstractmethod
    async def list_for_owner(self, owner_id: str) -> list[NotificationRule]:
        """Return an owner's rules, oldest first."""

    @abstractmethod
    async def list_enabled_for_owner(self, owner_id: str) -> list[NotificationRule]:
        """Return an owner's enabled rules, oldest first."""

    @abstractmethod
    async def get(self, owner_id: str, rule_id: UUID) -> NotificationRule | None:
        """Return one of an owner's rules."""

    @abstractmethod
    async def create(self, rule: NotificationRule) -> NotificationRule:
        """Persist a new rule."""

    @abstractmethod
    async def create_if_absent(self, rule: NotificationRule) -> NotificationRule | None:
        """Persist ``rule`` unless one with its id exists; ``None`` when it did."""

    @abstractmethod
    async def update(self, rule: NotificationRule) -> NotificationRule | None:
        """Replace an owner's rule; ``None`` when it no longer exists."""

    @abstractmethod
    async def delete(self, owner_id: str, rule_id: UUID) -> bool:
        """Delete an owner's rule (and, by cascade, its deliveries)."""


class NotificationDeliveryRepository(ABC):
    """The delivery outbox. Rows are created by ``NotificationRepository.project``.

    Claim and settle follow Ting's A2A outbox: ``FOR UPDATE SKIP LOCKED`` claims,
    a lease, and dispatcher-side exponential backoff. Every settle call is fenced
    by ``(delivery.id, delivery.attempts)`` of the claim it settles and returns
    whether it applied.
    """

    @abstractmethod
    async def claim_due(self, *, limit: int, lease_seconds: float) -> list[ClaimedDelivery]:
        """Lease up to ``limit`` due rows.

        Due means ``pending``/``failed`` with ``next_attempt_at <= now()``, or
        ``claimed`` with an expired lease (a worker died). Each claimed row becomes
        ``claimed`` with ``lease_until = now() + lease_seconds`` and ``attempts + 1``.
        """

    @abstractmethod
    async def mark_delivered(self, delivery: NotificationDelivery) -> bool:
        """Settle a claim as ``delivered``."""

    @abstractmethod
    async def mark_failed(
        self, delivery: NotificationDelivery, *, error: str, retry_at: datetime | None
    ) -> bool:
        """Settle a claim as ``failed`` (retried at ``retry_at``), or ``dead`` if None."""

    @abstractmethod
    async def mark_suppressed(self, delivery: NotificationDelivery, *, reason: str) -> bool:
        """Settle a claim as ``suppressed`` (quiet hours or rate limit held it back)."""

    @abstractmethod
    async def list_for_notification(self, notification_id: UUID) -> list[NotificationDelivery]:
        """Return a notification's delivery rows, oldest first."""

    @abstractmethod
    async def count_delivered_since(self, rule_id: UUID, since: datetime) -> int:
        """How many of a rule's deliveries were ``delivered`` at or after ``since``.

        The dispatcher's per-rule rate limit reads it at claim time.
        """


class NotificationSinkProvider(ABC):
    """Finds the sink that delivers a rule's notifications.

    A rule with an ``integration_connection_id`` delivers through that owner's
    messaging integration; any other rule names a configured sink. Raises
    ``niuu.ports.notifications.NotificationSinkUnavailableError`` when neither
    resolves. Every sink returned by :meth:`open` goes back through
    :meth:`release`, which closes per-use sinks and keeps shared ones.
    """

    @abstractmethod
    async def open(self, rule: NotificationRule) -> NotificationSink:
        """Return the sink for ``rule``."""

    @abstractmethod
    async def release(self, sink: NotificationSink) -> None:
        """Give back a sink obtained from :meth:`open`."""

    @abstractmethod
    async def close(self) -> None:
        """Close every shared sink (on shutdown)."""


class NotificationRecorder(ABC):
    """What the session service needs to record a session's system notifications."""

    @abstractmethod
    async def record_attention(
        self,
        session: Session,
        *,
        state_since: datetime,
        kind: str,
        prompt: str,
        request_id: str,
    ) -> Notification | None:
        """Record (idempotently) that ``session`` is waiting for its owner."""

    @abstractmethod
    async def retire_attention(self, session: Session) -> int:
        """Acknowledge the owner's unread "needs your input" items for ``session``.

        Called when the session stops waiting; returns how many were acknowledged.
        """
