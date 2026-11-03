"""PostgreSQL adapter for artifact publications and bursts (F1, F1n). Raw asyncpg SQL.

Every recording transaction takes one advisory lock first. That serializes feed-seq
allocation with commit (a reader paging ``after=<seq>`` never skips a late commit) and
makes burst membership race-free: the window lookup, the new or widened burst and the
membership row commit together. Emission locks the burst row, rechecks it, and writes the
notice through the notification repository's own SQL in the same transaction.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from niuu.domain.artifact_reports import REPORT_ARTIFACT_TOOL_NAME
from volundr.adapters.outbound._jsonb import scrub_text
from volundr.adapters.outbound.postgres_notifications import (
    _UNAVAILABLE_ERRORS,
    FEED_ORDER_LOCK_SQL,
    INSERT_NOTIFICATION_SQL,
    PostgresNotificationRepository,
    _candidate_args,
    row_to_notification,
)
from volundr.domain.artifact_ports import (
    ArtifactPublicationRepository,
    ArtifactStoreUnavailableError,
    EmitOutcome,
    EmitResult,
    LogCursor,
)
from volundr.domain.artifacts import (
    AGENT_NOTICE_REASON,
    ARTIFACT_TOOL_NAME,
    PRESENT_FILE_TOOL_NAME,
    ArtifactPublication,
    ArtifactType,
    Burst,
    BurstPolicy,
    Origin,
    Provider,
    PublicationCandidate,
    PublicationSource,
    choose_burst,
    opened,
    publication_id,
    revision_id,
    suppression_bounds,
    widened,
)
from volundr.domain.models import SessionLogEntry
from volundr.domain.notifications import NotificationCandidate, NotificationScope

# A retry of the whole (idempotent) batch is safe and fixes these: answer 503 at ingest.
_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    *_UNAVAILABLE_ERRORS,
    asyncpg.exceptions.DeadlockDetectedError,
    asyncpg.exceptions.SerializationError,
)

ARTIFACT_FEED_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtext('forge_artifact_publications.feed_order'))"
)

INSERT_PUBLICATION_SQL = """INSERT INTO forge_artifact_publications
    (publication_id, host, session_id, session_seq, ordinal, source, provider, provider_key,
     provider_version, provider_seq, revision_id, title, url, at, origin, tool_use_id,
     artifact_type, type_url, mime, subagent, version_label)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18,
        $19, $20, $21)
ON CONFLICT DO NOTHING
RETURNING *"""

WINDOW_BURSTS_SQL = """SELECT * FROM forge_artifact_bursts
WHERE host = $1 AND provider_key = $2 AND origin = $3
  AND started_at - $5::interval <= $4 AND $4 <= last_at + $5::interval
FOR UPDATE"""

INSERT_BURST_SQL = """INSERT INTO forge_artifact_bursts
    (burst_id, host, provider_key, origin, started_at, last_at, publications,
     seed_session_id, seed_session_seq, due_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)"""

WIDEN_BURST_SQL = """UPDATE forge_artifact_bursts
SET started_at = $2, last_at = $3, publications = $4,
    rev = nextval('forge_artifact_bursts_rev_seq'), updated_at = now()
WHERE burst_id = $1"""

INSERT_MEMBER_SQL = """INSERT INTO forge_artifact_burst_publications
    (burst_id, publication_id, bridged)
VALUES ($1, $2, $3::uuid[])"""

SELECT_PUBLICATIONS_SQL = """SELECT p.*, m.burst_id
FROM forge_artifact_publications p
LEFT JOIN forge_artifact_burst_publications m ON m.publication_id = p.publication_id"""

DUE_BURSTS_SQL = """SELECT * FROM forge_artifact_bursts
WHERE due_at IS NOT NULL AND due_at <= $1 AND notification_id IS NULL
  AND suppressed_by IS NULL AND suppress_reason IS NULL
ORDER BY due_at, burst_id
LIMIT $2"""

LOCK_BURST_SQL = "SELECT * FROM forge_artifact_bursts WHERE burst_id = $1 FOR UPDATE"

# An agent notice (any session, model/bursts.md) whose link names the artifact, created
# from the burst window's start up to the emission. Slice 0's own notices are `system`.
SUPPRESSOR_SQL = """SELECT n.id FROM forge_notifications n
WHERE n.source = 'agent' AND n.created_at >= $2 AND n.created_at <= $3
  AND COALESCE(n.correlation_id, '') NOT LIKE 'art-burst:%'
  AND jsonb_typeof(n.links) = 'array'
  AND EXISTS (
    SELECT 1 FROM jsonb_array_elements(n.links) l
    WHERE l->>'url' ~ ('^https://claude\\.ai/(code/)?artifact/' || $1 || '([/?#].*)?$')
  )
ORDER BY n.created_at, n.id
LIMIT 1"""

SUPPRESS_SQL = """UPDATE forge_artifact_bursts
SET suppressed_by = $2, suppress_reason = $3,
    rev = nextval('forge_artifact_bursts_rev_seq'), updated_at = now()
WHERE burst_id = $1"""

EMITTED_SQL = """UPDATE forge_artifact_bursts
SET notification_id = $2, emitted_at = $3,
    rev = nextval('forge_artifact_bursts_rev_seq'), updated_at = now()
WHERE burst_id = $1"""

SETTLE_SQL = """UPDATE forge_artifact_bursts
SET suppress_reason = $2, rev = nextval('forge_artifact_bursts_rev_seq'), updated_at = now()
WHERE burst_id = $1 AND notification_id IS NULL AND suppressed_by IS NULL
  AND suppress_reason IS NULL"""

# The internal backfill: stored rows that can carry a publication, in (session, seq)
# order. It reads the durable table directly; the public /log hides conversation turns.
SCAN_LOG_SQL = f"""SELECT session_id, seq, kind, role, request_id, payload, ts
FROM session_event_log
WHERE ($1::uuid IS NULL OR (session_id, seq) > ($1::uuid, $2::bigint))
  AND ($3::uuid IS NULL OR session_id = $3::uuid)
  AND ($4::timestamptz IS NULL OR ts >= $4::timestamptz)
  AND ($5::timestamptz IS NULL OR ts < $5::timestamptz)
  AND (
    (kind = 'claude_hook'
      AND payload->>'hook_event_name' = 'PostToolUse'
      AND payload->'payload'->>'tool_name' = '{ARTIFACT_TOOL_NAME}')
    OR (kind = 'conversation.turn' AND (
      payload->'turn'->'metadata'->>'kind' IN ('notification', 'artifact_report')
      OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(
          CASE WHEN jsonb_typeof(payload->'turn'->'parts') = 'array'
               THEN payload->'turn'->'parts' ELSE '[]'::jsonb END) part
        WHERE part->>'name' IN ('{PRESENT_FILE_TOOL_NAME}', '{REPORT_ARTIFACT_TOOL_NAME}'))))
  )
ORDER BY session_id, seq
LIMIT $6"""


def _publication_args(
    host: str, candidate: PublicationCandidate, origin: Origin
) -> tuple[Any, ...]:
    return (
        publication_id(host, candidate.session_id, candidate.session_seq, candidate.ordinal),
        host,
        candidate.session_id,
        candidate.session_seq,
        candidate.ordinal,
        candidate.source.value,
        candidate.provider.value,
        candidate.provider_key,
        scrub_text(candidate.provider_version),
        candidate.provider_seq,
        revision_id(candidate, host),
        scrub_text(candidate.title),
        scrub_text(candidate.url),
        candidate.at,
        origin.value,
        scrub_text(candidate.tool_use_id),
        candidate.artifact_type.value,
        scrub_text(candidate.type_url),
        scrub_text(candidate.mime),
        candidate.subagent,
        scrub_text(candidate.version_label),
    )


def row_to_publication(
    row: asyncpg.Record | dict, burst_id: UUID | None = None
) -> ArtifactPublication:
    keys = row.keys() if hasattr(row, "keys") else ()
    return ArtifactPublication(
        publication_id=row["publication_id"],
        seq=row["seq"],
        host=row["host"],
        session_id=row["session_id"],
        session_seq=row["session_seq"],
        ordinal=row["ordinal"],
        source=PublicationSource(row["source"]),
        provider=Provider(row["provider"]),
        provider_key=row["provider_key"],
        revision_id=row["revision_id"],
        at=row["at"],
        origin=Origin(row["origin"]),
        url=row["url"],
        title=row["title"],
        tool_use_id=row["tool_use_id"],
        provider_version=row["provider_version"],
        provider_seq=row["provider_seq"],
        artifact_type=ArtifactType(row["artifact_type"]),
        type_url=row["type_url"],
        mime=row["mime"],
        subagent=row["subagent"],
        version_label=row["version_label"],
        burst_id=burst_id
        if burst_id is not None
        else (row["burst_id"] if "burst_id" in keys else None),
        created_at=row["created_at"],
    )


def row_to_burst(row: asyncpg.Record | dict) -> Burst:
    return Burst(
        burst_id=row["burst_id"],
        host=row["host"],
        provider_key=row["provider_key"],
        origin=Origin(row["origin"]),
        started_at=row["started_at"],
        last_at=row["last_at"],
        publications=row["publications"],
        seed_session_id=row["seed_session_id"],
        seed_session_seq=row["seed_session_seq"],
        due_at=row["due_at"],
        notification_id=row["notification_id"],
        suppressed_by=row["suppressed_by"],
        suppress_reason=row["suppress_reason"],
        emitted_at=row["emitted_at"],
    )


def _payload(value: Any) -> dict:
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return value if isinstance(value, dict) else {}


def _session_scope(scope: NotificationScope | None, params: list[Any], alias: str) -> str:
    """Readers see publications of sessions they own; admins also their tenant's and
    untenanted ones (the notification feed's rule, ``notification_visible_to``)."""
    if scope is None:
        return "TRUE"
    params.append(scope.user_id)
    owner = f"s.owner_id = ${len(params)}"
    if scope.is_admin:
        params.append(scope.tenant_id)
        owner = f"({owner} OR COALESCE(s.tenant_id, '') IN ('', ${len(params)}))"
    return f"EXISTS (SELECT 1 FROM sessions s WHERE s.id = {alias} AND {owner})"


class PostgresArtifactPublicationRepository(ArtifactPublicationRepository):
    def __init__(self, pool: asyncpg.Pool, **_extra: object) -> None:
        self._pool = pool

    async def record(
        self,
        host: str,
        items: list[tuple[PublicationCandidate, Origin]],
        *,
        policy: BurstPolicy,
    ) -> list[ArtifactPublication]:
        if not items:
            return []
        try:
            return await self._record(host, items, policy=policy)
        except _RETRYABLE_ERRORS as exc:
            raise ArtifactStoreUnavailableError(str(exc) or type(exc).__name__) from exc

    async def _record(
        self,
        host: str,
        items: list[tuple[PublicationCandidate, Origin]],
        *,
        policy: BurstPolicy,
    ) -> list[ArtifactPublication]:
        ordered = sorted(
            items, key=lambda item: (str(item[0].session_id), item[0].session_seq, item[0].ordinal)
        )
        created: list[ArtifactPublication] = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(ARTIFACT_FEED_LOCK_SQL)
                for candidate, origin in ordered:
                    row = await conn.fetchrow(
                        INSERT_PUBLICATION_SQL, *_publication_args(host, candidate, origin)
                    )
                    if row is None:
                        continue  # recorded before: its membership is final (B8)
                    burst_id = None
                    if candidate.forms_bursts:
                        burst_id = await self._join(conn, host, candidate, origin, row, policy)
                    created.append(row_to_publication(row, burst_id))
        return sorted(created, key=lambda publication: publication.seq)

    @staticmethod
    async def _join(
        conn: asyncpg.Connection,
        host: str,
        candidate: PublicationCandidate,
        origin: Origin,
        row: asyncpg.Record,
        policy: BurstPolicy,
    ) -> UUID:
        rows = await conn.fetch(
            WINDOW_BURSTS_SQL,
            host,
            candidate.provider_key,
            origin.value,
            candidate.at,
            policy.window,
        )
        choice = choose_burst([row_to_burst(r) for r in rows], candidate.at, policy.window)
        if choice.burst is None:
            burst = opened(host, candidate, origin, policy.hold)
            await conn.execute(
                INSERT_BURST_SQL,
                burst.burst_id,
                burst.host,
                burst.provider_key,
                burst.origin.value,
                burst.started_at,
                burst.last_at,
                burst.publications,
                burst.seed_session_id,
                burst.seed_session_seq,
                burst.due_at,
            )
        else:
            burst = widened(choice.burst, candidate.at)
            await conn.execute(
                WIDEN_BURST_SQL, burst.burst_id, burst.started_at, burst.last_at, burst.publications
            )
        await conn.execute(
            INSERT_MEMBER_SQL, burst.burst_id, row["publication_id"], list(choice.bridged)
        )
        return burst.burst_id

    async def list_after(
        self, scope: NotificationScope | None, *, after: int, limit: int
    ) -> list[ArtifactPublication]:
        params: list[Any] = [after]
        visible = _session_scope(scope, params, "p.session_id")
        params.append(limit)
        sql = (
            f"{SELECT_PUBLICATIONS_SQL} WHERE p.seq > $1 AND {visible} "
            f"ORDER BY p.seq LIMIT ${len(params)}"
        )
        rows = await self._pool.fetch(sql, *params)
        return [row_to_publication(row) for row in rows]

    async def list_bursts_after(
        self, scope: NotificationScope | None, *, after: int, limit: int
    ) -> list[tuple[int, Burst]]:
        params: list[Any] = [after]
        visible = _session_scope(scope, params, "b.seed_session_id")
        params.append(limit)
        sql = (
            f"SELECT b.* FROM forge_artifact_bursts b WHERE b.rev > $1 AND {visible} "
            f"ORDER BY b.rev LIMIT ${len(params)}"
        )
        rows = await self._pool.fetch(sql, *params)
        return [(row["rev"], row_to_burst(row)) for row in rows]

    async def get_burst(self, burst_id: UUID) -> Burst | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM forge_artifact_bursts WHERE burst_id = $1", burst_id
        )
        return row_to_burst(row) if row is not None else None

    async def burst_publications(
        self, burst_id: UUID, scope: NotificationScope | None = None
    ) -> list[ArtifactPublication]:
        params: list[Any] = [burst_id]
        visible = _session_scope(scope, params, "p.session_id")
        rows = await self._pool.fetch(
            f"{SELECT_PUBLICATIONS_SQL} WHERE m.burst_id = $1 AND {visible} ORDER BY p.at, p.seq",
            *params,
        )
        return [row_to_publication(row) for row in rows]

    async def due_bursts(self, now: datetime, *, limit: int) -> list[Burst]:
        rows = await self._pool.fetch(DUE_BURSTS_SQL, now, limit)
        return [row_to_burst(row) for row in rows]

    async def emit(
        self,
        burst_id: UUID,
        candidate: NotificationCandidate,
        *,
        now: datetime,
        policy: BurstPolicy,
    ) -> EmitResult:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Lock order everywhere: artifact feed lock, burst row, notification feed
                # lock. The first serializes ``rev`` with commit (a burst-feed reader never
                # skips a change); the last makes the suppression recheck see every agent
                # notice committed before this one can be inserted.
                await conn.execute(ARTIFACT_FEED_LOCK_SQL)
                locked = await conn.fetchrow(LOCK_BURST_SQL, burst_id)
                if locked is None:
                    return EmitResult(outcome=EmitOutcome.SETTLED)
                burst = row_to_burst(locked)
                if not burst.pending:
                    return EmitResult(outcome=EmitOutcome.SETTLED)
                await conn.execute(FEED_ORDER_LOCK_SQL)
                start, end = suppression_bounds(burst, policy.window, now)
                suppressor = await conn.fetchval(SUPPRESSOR_SQL, burst.provider_key, start, end)
                if suppressor is not None:
                    await conn.execute(SUPPRESS_SQL, burst_id, suppressor, AGENT_NOTICE_REASON)
                    return EmitResult(outcome=EmitOutcome.SUPPRESSED, suppressed_by=suppressor)
                row = await conn.fetchrow(INSERT_NOTIFICATION_SQL, *_candidate_args(candidate))
                if row is None:
                    # The dedupe key already holds this burst's notice: settle onto it.
                    await conn.execute(EMITTED_SQL, burst_id, candidate.id, now)
                    return EmitResult(outcome=EmitOutcome.SETTLED)
                notification = row_to_notification(row)
                await PostgresNotificationRepository._schedule_deliveries(conn, [notification])
                await conn.execute(EMITTED_SQL, burst_id, notification.id, now)
        return EmitResult(outcome=EmitOutcome.EMITTED, notification=notification)

    async def settle(self, burst_id: UUID, *, reason: str) -> bool:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(ARTIFACT_FEED_LOCK_SQL)
                status = await conn.execute(SETTLE_SQL, burst_id, reason)
        return status.endswith(" 1")

    async def scan_log(
        self,
        cursor: LogCursor,
        *,
        limit: int,
        since: datetime | None,
        until: datetime | None,
        session_id: UUID | None,
    ) -> list[SessionLogEntry]:
        rows = await self._pool.fetch(
            SCAN_LOG_SQL, cursor.session_id, cursor.seq, session_id, since, until, limit
        )
        return [
            SessionLogEntry(
                session_id=row["session_id"],
                seq=row["seq"],
                kind=row["kind"],
                payload=_payload(row["payload"]),
                ts=row["ts"],
                role=row["role"],
                request_id=row["request_id"],
            )
            for row in rows
        ]
