"""Artifact publications service (F1) and burst notices (F1n).

* **Ingest:** the session-log append projects every publication from the STORED rows,
  before the append is acknowledged (like notifications), so a producer retry re-runs it
  idempotently. Ingest marks a row ``live`` when it arrives within ``live_lag`` of its
  timestamp; anything older is ``backfill``.
* **Backfill:** a SQL replay of ``session_event_log`` (never the public ``/log`` route,
  which hides ``conversation.turn`` rows). Always ``backfill``, so it never notifies.
* **Hold:** a live burst is due ``hold`` after it opened. :meth:`emit_due` runs from
  Forge's background loop; each emission is one transaction that rechecks suppression,
  so a restart during the hold emits exactly once.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from niuu.domain.notifications import (
    MAX_TITLE_CHARS,
    NotificationDraft,
    NotificationKind,
    NotificationLink,
    NotificationLinkKind,
    NotificationSeverity,
)
from volundr.domain.artifact_ports import (
    ArtifactPublicationRepository,
    EmitOutcome,
    EmitResult,
    LogCursor,
)
from volundr.domain.artifacts import (
    EMIT_FAILED_REASON,
    SESSION_GONE_REASON,
    ArtifactPublication,
    Burst,
    BurstPolicy,
    Origin,
    PublicationCandidate,
    TypeCatalog,
    burst_correlation_id,
    burst_dedupe_key,
    classify_origin,
    is_publication_row,
    publications_from_entry,
)
from volundr.domain.models import Session, SessionLogEntry
from volundr.domain.notifications import Notification, NotificationCandidate, NotificationScope

logger = logging.getLogger(__name__)

SessionLookup = Callable[[UUID], Awaitable[Session | None]]


class BurstNoticeWriter(Protocol):
    """What emission needs from the notification service."""

    def burst_candidate(
        self, session: Session, *, dedupe_key: str, draft: NotificationDraft, metadata: dict
    ) -> NotificationCandidate: ...

    async def broadcast_created(self, notification: Notification) -> None: ...


@dataclass(frozen=True)
class BackfillReport:
    scanned_rows: int
    recorded: int
    last_session_id: UUID | None
    last_seq: int


class ArtifactService:
    def __init__(
        self,
        repository: ArtifactPublicationRepository,
        *,
        host: str,
        policy: BurstPolicy,
        catalog: TypeCatalog,
        notices: BurstNoticeWriter | None,
        session_lookup: SessionLookup | None,
        emit_batch_size: int,
        backfill_page_size: int,
        max_emit_failures: int = 5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not host:
            raise ValueError("an artifact host name is required")
        if emit_batch_size < 1 or backfill_page_size < 1:
            raise ValueError("batch sizes must be positive")
        self._repository = repository
        self._host = host
        self._policy = policy
        self._catalog = catalog
        self._notices = notices
        self._session_lookup = session_lookup
        self._emit_batch_size = emit_batch_size
        self._backfill_page_size = backfill_page_size
        self._max_emit_failures = max_emit_failures
        self._emit_failures: dict[UUID, int] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def host(self) -> str:
        return self._host

    @property
    def policy(self) -> BurstPolicy:
        return self._policy

    # -- capture -------------------------------------------------------------

    def is_projectable(self, payload: dict) -> bool:
        return is_publication_row(payload)

    def candidates(self, entries: list[SessionLogEntry]) -> list[PublicationCandidate]:
        found: list[PublicationCandidate] = []
        for entry in sorted(entries, key=lambda item: item.seq):
            found.extend(publications_from_entry(entry, self._catalog))
        return found

    async def project_log_entries(
        self, session: Session, entries: list[SessionLogEntry], *, seen_at: datetime
    ) -> list[ArtifactPublication]:
        """Record the publications in STORED rows of ``session``'s log."""
        items: list[tuple[PublicationCandidate, Origin]] = []
        for candidate in self.candidates(entries):
            if candidate.session_id != session.id:
                raise ValueError("Every projected log row must belong to the session")
            items.append((candidate, classify_origin(candidate.at, seen_at, self._policy.live_lag)))
        return await self.record(items)

    async def record(
        self, items: list[tuple[PublicationCandidate, Origin]]
    ) -> list[ArtifactPublication]:
        if not items:
            return []
        return await self._repository.record(self._host, items, policy=self._policy)

    async def backfill(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: UUID | None = None,
        cursor: LogCursor | None = None,
        max_pages: int | None = None,
    ) -> BackfillReport:
        """Replay stored log rows into publications with ``origin = backfill``.

        It never reads rows younger than ``live_lag``: those still belong to live ingest,
        and recording one first as ``backfill`` would silence its burst.
        """
        horizon = self._clock() - self._policy.live_lag
        until = horizon if until is None or until > horizon else until
        position = cursor or LogCursor()
        scanned = recorded = pages = 0
        while max_pages is None or pages < max_pages:
            rows = await self._repository.scan_log(
                position,
                limit=self._backfill_page_size,
                since=since,
                until=until,
                session_id=session_id,
            )
            pages += 1
            if not rows:
                break
            scanned += len(rows)
            items = [(candidate, Origin.BACKFILL) for candidate in self.candidates(rows)]
            recorded += len(await self.record(items))
            last = max(rows, key=lambda row: (str(row.session_id), row.seq))
            position = LogCursor(session_id=last.session_id, seq=last.seq)
            if len(rows) < self._backfill_page_size:
                break
        return BackfillReport(
            scanned_rows=scanned,
            recorded=recorded,
            last_session_id=position.session_id,
            last_seq=position.seq,
        )

    # -- reads -----------------------------------------------------------------

    async def list_after(
        self, scope: NotificationScope | None, *, after: int, limit: int
    ) -> list[ArtifactPublication]:
        return await self._repository.list_after(scope, after=after, limit=limit)

    async def list_bursts_after(
        self, scope: NotificationScope | None, *, after: int, limit: int
    ) -> list[tuple[int, Burst]]:
        return await self._repository.list_bursts_after(scope, after=after, limit=limit)

    async def get_burst(self, burst_id: UUID) -> Burst | None:
        return await self._repository.get_burst(burst_id)

    async def burst_publications(
        self, scope: NotificationScope | None, burst_id: UUID
    ) -> list[ArtifactPublication]:
        return await self._repository.burst_publications(burst_id, scope)

    # -- the durable hold ------------------------------------------------------

    async def emit_due(self, now: datetime) -> list[EmitResult]:
        """Emit (or suppress) every live burst whose hold has passed."""
        if self._notices is None or self._session_lookup is None:
            return []
        results: list[EmitResult] = []
        for burst in await self._repository.due_bursts(now, limit=self._emit_batch_size):
            try:
                result = await self._emit_one(burst, now)
            except Exception:
                result = await self._failed(burst)
            if result is not None:
                results.append(result)
        return results

    async def _failed(self, burst: Burst) -> EmitResult | None:
        """One bad burst never stalls the others; after repeated failures it is settled."""
        failures = self._emit_failures.get(burst.burst_id, 0) + 1
        self._emit_failures[burst.burst_id] = failures
        logger.exception("burst %s: emission failed (%d)", burst.burst_id, failures)
        if failures < self._max_emit_failures:
            return None
        self._emit_failures.pop(burst.burst_id, None)
        logger.error(
            "burst %s: settled without a notice after %d failed emissions "
            "(artifacts.max_emit_failures); its suppress_reason is %r",
            burst.burst_id,
            failures,
            EMIT_FAILED_REASON,
        )
        await self._repository.settle(burst.burst_id, reason=EMIT_FAILED_REASON)
        return EmitResult(outcome=EmitOutcome.SETTLED)

    async def _emit_one(self, burst: Burst, now: datetime) -> EmitResult | None:
        assert self._notices is not None and self._session_lookup is not None
        members = await self._repository.burst_publications(burst.burst_id)
        if not members:
            logger.warning("burst %s has no publications; skipped", burst.burst_id)
            return None
        session = await self._session_lookup(burst.seed_session_id)
        if session is None:
            logger.warning(
                "burst %s: seed session %s is gone; settled without a notice",
                burst.burst_id,
                burst.seed_session_id,
            )
            await self._repository.settle(burst.burst_id, reason=SESSION_GONE_REASON)
            return EmitResult(outcome=EmitOutcome.SETTLED)
        latest = max(members, key=lambda pub: (pub.at, pub.seq))
        candidate = self._notices.burst_candidate(
            session,
            dedupe_key=burst_dedupe_key(burst.burst_id),
            draft=_burst_draft(burst, latest, session),
            metadata={
                "burst_id": str(burst.burst_id),
                "provider_key": burst.provider_key,
                "publication_id": str(latest.publication_id),
            },
        )
        result = await self._repository.emit(
            burst.burst_id, candidate, now=now, policy=self._policy
        )
        if result.outcome is EmitOutcome.EMITTED and result.notification is not None:
            await self._notices.broadcast_created(result.notification)
        return result


def _burst_title(title: str | None) -> str:
    base = (title or "Artifact").strip() or "Artifact"
    suffix = " published"
    if len(base) + len(suffix) <= MAX_TITLE_CHARS:
        return base + suffix
    return base[: MAX_TITLE_CHARS - len(suffix) - 1].rstrip() + "…" + suffix


def _burst_draft(burst: Burst, latest: ArtifactPublication, session: Session) -> NotificationDraft:
    """Design §V1 rule 3: ``<title> published``, the session's name, one artifact link."""
    title = _burst_title(latest.title)
    return NotificationDraft(
        kind=NotificationKind.MILESTONE,
        severity=NotificationSeverity.INFO,
        title=title,
        body=session.name,
        links=[
            NotificationLink(
                label=(latest.title or "Artifact")[:200],
                url=latest.url,
                kind=NotificationLinkKind.ARTIFACT,
            )
        ],
        correlation_id=burst_correlation_id(burst.burst_id),
    )
