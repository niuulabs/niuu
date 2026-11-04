"""Port for artifact publications and their bursts (F1, F1n)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from volundr.domain.artifacts import (
    ArtifactPublication,
    Burst,
    BurstPolicy,
    Origin,
    PublicationCandidate,
)
from volundr.domain.models import SessionLogEntry
from volundr.domain.notifications import Notification, NotificationCandidate, NotificationScope


class ArtifactStoreUnavailableError(RuntimeError):
    """The store could not be reached; nothing was committed and a retry is safe."""


class EmitOutcome(StrEnum):
    EMITTED = "emitted"
    SUPPRESSED = "suppressed"
    SETTLED = "settled"  # already emitted or suppressed by an earlier attempt


@dataclass(frozen=True)
class EmitResult:
    outcome: EmitOutcome
    notification: Notification | None = None
    suppressed_by: UUID | None = None


@dataclass(frozen=True)
class LogCursor:
    """Where a backfill scan stands: the last ``(session_id, seq)`` it read."""

    session_id: UUID | None = None
    seq: int = 0


class ArtifactPublicationRepository(ABC):
    """Durable publications, bursts and their membership.

    ``record`` commits new publications together with their burst membership: a
    publication is never visible without the burst it joined. ``emit`` is the durable
    hold's single emission transaction (recheck, notice, deliveries, burst outcome).
    """

    @abstractmethod
    async def record(
        self,
        host: str,
        items: list[tuple[PublicationCandidate, Origin]],
        *,
        policy: BurstPolicy,
    ) -> list[ArtifactPublication]:
        """Insert unseen publications; assign burst-forming ones to their burst.

        Idempotent: an already-recorded occurrence changes nothing (its membership is
        final). Returns the newly inserted rows only, in feed order.
        """

    @abstractmethod
    async def list_after(
        self, scope: NotificationScope | None, *, after: int, limit: int
    ) -> list[ArtifactPublication]:
        """Publications with ``seq > after`` the reader may see, oldest first."""

    @abstractmethod
    async def list_bursts_after(
        self, scope: NotificationScope | None, *, after: int, limit: int
    ) -> list[tuple[int, Burst]]:
        """``(rev, burst)`` for bursts changed after ``rev``, in change order."""

    @abstractmethod
    async def get_burst(self, burst_id: UUID) -> Burst | None:
        """One burst, or ``None``."""

    @abstractmethod
    async def burst_publications(
        self, burst_id: UUID, scope: NotificationScope | None = None
    ) -> list[ArtifactPublication]:
        """A burst's members the reader may see (all with no scope), oldest first."""

    @abstractmethod
    async def due_bursts(self, now: datetime, *, limit: int) -> list[Burst]:
        """Live bursts whose hold has passed and whose outcome is still open."""

    @abstractmethod
    async def emit(
        self,
        burst_id: UUID,
        candidate: NotificationCandidate,
        *,
        now: datetime,
        policy: BurstPolicy,
    ) -> EmitResult:
        """In one transaction: lock the burst, recheck it is pending, recheck suppression
        by an agent notice linking the artifact, then either record the suppression or
        insert the notice (with its deliveries) and the burst's notification id."""

    @abstractmethod
    async def settle(self, burst_id: UUID, *, reason: str) -> bool:
        """Close a pending burst without a notice (``suppress_reason``); False if not pending."""

    @abstractmethod
    async def scan_log(
        self,
        cursor: LogCursor,
        *,
        limit: int,
        since: datetime | None,
        until: datetime | None,
        session_id: UUID | None,
    ) -> list[SessionLogEntry]:
        """Stored log rows that may carry a publication, after ``cursor`` in
        ``(session_id, seq)`` order (the internal backfill, never the public route)."""
