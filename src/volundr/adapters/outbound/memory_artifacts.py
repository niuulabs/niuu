"""In-memory artifact publication store: the same rule as Postgres, for tests and replays.

It applies the domain's membership rule (:func:`choose_burst`) exactly as the Postgres
adapter does, and keeps a minimal notice store so the hold's suppression recheck and
emission can be exercised without a database (the golden replay, B1-B9).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from volundr.domain.artifact_ports import (
    ArtifactPublicationRepository,
    EmitOutcome,
    EmitResult,
    LogCursor,
)
from volundr.domain.artifacts import (
    AGENT_NOTICE_REASON,
    ArtifactPublication,
    Burst,
    BurstPolicy,
    Origin,
    PublicationCandidate,
    announces,
    choose_burst,
    opened,
    publication_id,
    revision_id,
    widened,
)
from volundr.domain.models import SessionLogEntry
from volundr.domain.notifications import Notification, NotificationCandidate, NotificationScope


@dataclass
class _State:
    publications: dict[UUID, ArtifactPublication] = field(default_factory=dict)
    bursts: dict[UUID, Burst] = field(default_factory=dict)
    burst_rev: dict[UUID, int] = field(default_factory=dict)
    members: dict[UUID, UUID] = field(default_factory=dict)  # publication -> burst
    bridges: dict[UUID, tuple[UUID, ...]] = field(default_factory=dict)
    notices: list[Notification] = field(default_factory=list)
    log: list[SessionLogEntry] = field(default_factory=list)
    seq: int = 0
    rev: int = 0
    notice_seq: int = 0


class InMemoryArtifactPublicationRepository(ArtifactPublicationRepository):
    def __init__(self) -> None:
        self._state = _State()

    # -- test helpers ------------------------------------------------------------

    def restarted(self) -> InMemoryArtifactPublicationRepository:
        """A new repository over a copy of the persisted state (what a restart reloads)."""
        clone = InMemoryArtifactPublicationRepository()
        clone._state = copy.deepcopy(self._state)
        return clone

    def add_notice(self, notification: Notification) -> None:
        """A notification another path recorded (an agent's notify, say)."""
        self._state.notices.append(notification)

    def add_log_rows(self, rows: list[SessionLogEntry]) -> None:
        self._state.log.extend(rows)

    @property
    def bursts(self) -> list[Burst]:
        return list(self._state.bursts.values())

    @property
    def notices(self) -> list[Notification]:
        return list(self._state.notices)

    def bridges_of(self, publication: UUID) -> tuple[UUID, ...]:
        return self._state.bridges.get(publication, ())

    def next_notice_seq(self) -> int:
        self._state.notice_seq += 1
        return self._state.notice_seq

    # -- the port ----------------------------------------------------------------

    async def record(
        self,
        host: str,
        items: list[tuple[PublicationCandidate, Origin]],
        *,
        policy: BurstPolicy,
    ) -> list[ArtifactPublication]:
        state = self._state
        created: list[ArtifactPublication] = []
        ordered = sorted(
            items, key=lambda item: (str(item[0].session_id), item[0].session_seq, item[0].ordinal)
        )
        for candidate, origin in ordered:
            pid = publication_id(
                host, candidate.session_id, candidate.session_seq, candidate.ordinal
            )
            if pid in state.publications:
                continue
            state.seq += 1
            burst_id = (
                self._join(host, candidate, origin, pid, policy) if candidate.forms_bursts else None
            )
            publication = ArtifactPublication(
                publication_id=pid,
                seq=state.seq,
                host=host,
                session_id=candidate.session_id,
                session_seq=candidate.session_seq,
                ordinal=candidate.ordinal,
                source=candidate.source,
                provider=candidate.provider,
                provider_key=candidate.provider_key,
                revision_id=revision_id(candidate, host),
                at=candidate.at,
                origin=origin,
                url=candidate.url,
                title=candidate.title,
                tool_use_id=candidate.tool_use_id,
                provider_version=candidate.provider_version,
                provider_seq=candidate.provider_seq,
                artifact_type=candidate.artifact_type,
                type_url=candidate.type_url,
                mime=candidate.mime,
                subagent=candidate.subagent,
                version_label=candidate.version_label,
                burst_id=burst_id,
                created_at=candidate.at,
            )
            state.publications[pid] = publication
            created.append(publication)
        return created

    def _join(
        self,
        host: str,
        candidate: PublicationCandidate,
        origin: Origin,
        pid: UUID,
        policy: BurstPolicy,
    ) -> UUID:
        state = self._state
        same_key = [
            burst
            for burst in state.bursts.values()
            if burst.host == host
            and burst.provider_key == candidate.provider_key
            and burst.origin is origin
        ]
        choice = choose_burst(same_key, candidate.at, policy.window)
        burst = (
            opened(host, candidate, origin, policy.hold)
            if choice.burst is None
            else widened(choice.burst, candidate.at)
        )
        self._store_burst(burst)
        state.members[pid] = burst.burst_id
        state.bridges[pid] = choice.bridged
        return burst.burst_id

    def _store_burst(self, burst: Burst) -> None:
        self._state.rev += 1
        self._state.bursts[burst.burst_id] = burst
        self._state.burst_rev[burst.burst_id] = self._state.rev

    async def list_after(
        self, scope: NotificationScope | None, *, after: int, limit: int
    ) -> list[ArtifactPublication]:
        rows = sorted(self._state.publications.values(), key=lambda pub: pub.seq)
        return [pub for pub in rows if pub.seq > after][:limit]

    async def list_bursts_after(
        self, scope: NotificationScope | None, *, after: int, limit: int
    ) -> list[tuple[int, Burst]]:
        rows = sorted(
            ((rev, self._state.bursts[bid]) for bid, rev in self._state.burst_rev.items()),
            key=lambda item: item[0],
        )
        return [item for item in rows if item[0] > after][:limit]

    async def get_burst(self, burst_id: UUID) -> Burst | None:
        return self._state.bursts.get(burst_id)

    async def burst_publications(
        self, burst_id: UUID, scope: NotificationScope | None = None
    ) -> list[ArtifactPublication]:
        members = [
            self._state.publications[pid]
            for pid, bid in self._state.members.items()
            if bid == burst_id
        ]
        return sorted(members, key=lambda pub: (pub.at, pub.seq))

    async def due_bursts(self, now: datetime, *, limit: int) -> list[Burst]:
        due = [
            burst
            for burst in self._state.bursts.values()
            if burst.pending and burst.due_at is not None and burst.due_at <= now
        ]
        due.sort(key=lambda burst: (burst.due_at, str(burst.burst_id)))
        return due[:limit]

    async def emit(
        self,
        burst_id: UUID,
        candidate: NotificationCandidate,
        *,
        now: datetime,
        policy: BurstPolicy,
    ) -> EmitResult:
        burst = self._state.bursts.get(burst_id)
        if burst is None or not burst.pending:
            return EmitResult(outcome=EmitOutcome.SETTLED)
        for notice in sorted(self._state.notices, key=lambda n: (n.created_at, str(n.id))):
            if announces(
                source=notice.source.value,
                correlation_id=notice.correlation_id,
                link_urls=[link.url for link in notice.links],
                created_at=notice.created_at,
                burst=burst,
                window=policy.window,
                until=now,
            ):
                self._store_burst(
                    replace(burst, suppressed_by=notice.id, suppress_reason=AGENT_NOTICE_REASON)
                )
                return EmitResult(outcome=EmitOutcome.SUPPRESSED, suppressed_by=notice.id)
        if any(notice.dedupe_key == candidate.dedupe_key for notice in self._state.notices):
            self._store_burst(replace(burst, notification_id=candidate.id, emitted_at=now))
            return EmitResult(outcome=EmitOutcome.SETTLED)
        notification = Notification(
            id=candidate.id,
            seq=self.next_notice_seq(),
            dedupe_key=candidate.dedupe_key,
            session_id=candidate.session_id,
            session_seq=candidate.session_seq,
            session_name=candidate.session_name,
            owner_id=candidate.owner_id,
            tenant_id=candidate.tenant_id,
            project_id=candidate.project_id,
            kind=candidate.kind,
            severity=candidate.severity,
            source=candidate.source,
            title=candidate.title,
            body=candidate.body,
            links=candidate.links,
            engine=candidate.engine,
            model=candidate.model,
            correlation_id=candidate.correlation_id,
            metadata=candidate.metadata,
            created_at=now,
        )
        self._state.notices.append(notification)
        self._store_burst(replace(burst, notification_id=notification.id, emitted_at=now))
        return EmitResult(outcome=EmitOutcome.EMITTED, notification=notification)

    async def settle(self, burst_id: UUID, *, reason: str) -> bool:
        burst = self._state.bursts.get(burst_id)
        if burst is None or not burst.pending:
            return False
        self._store_burst(replace(burst, suppress_reason=reason))
        return True

    async def scan_log(
        self,
        cursor: LogCursor,
        *,
        limit: int,
        since: datetime | None,
        until: datetime | None,
        session_id: UUID | None,
    ) -> list[SessionLogEntry]:
        def key(row: SessionLogEntry) -> tuple[str, int]:
            return (str(row.session_id), row.seq)

        start = (str(cursor.session_id), cursor.seq) if cursor.session_id else None
        rows = [
            row
            for row in sorted(self._state.log, key=key)
            if (start is None or key(row) > start)
            and (session_id is None or row.session_id == session_id)
            and (since is None or row.ts >= since)
            and (until is None or row.ts < until)
        ]
        return rows[:limit]
