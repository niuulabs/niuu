"""PostgreSQL adapters for Forge notifications: the feed, rules and the delivery outbox.

Raw asyncpg SQL. The three repositories live together because the projection writes
across all three tables in one transaction: a notification is committed only with
its scheduled deliveries (committed ⇒ scheduled).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from volundr.adapters.outbound._jsonb import dumps_jsonb, scrub_text
from volundr.domain.notification_ports import (
    NotificationDeliveryRepository,
    NotificationRepository,
    NotificationRuleRepository,
)
from volundr.domain.notifications import (
    ClaimedDelivery,
    DeliveryStatus,
    Notification,
    NotificationCandidate,
    NotificationDelivery,
    NotificationNotFoundError,
    NotificationPage,
    NotificationQuery,
    NotificationReadStateConflictError,
    NotificationRule,
    NotificationRuleMatch,
    NotificationScope,
    NotificationStoreUnavailableError,
    ReadWatermark,
    rules_matching,
)

# Connection-level failures: the projection did not commit and a retry is safe.
_UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.TooManyConnectionsError,
    ConnectionError,
    TimeoutError,
)

# Serializes seq allocation with commit: every projection transaction takes this
# lock before inserting, so seqs become visible in increasing order and a reader
# paging ``after=<seq>`` can never skip a row that commits late.
FEED_ORDER_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('forge_notifications.feed_order'))"

INSERT_NOTIFICATION_SQL = """INSERT INTO forge_notifications
    (id, dedupe_key, session_id, session_seq, session_name, owner_id, tenant_id,
     project_id, kind, severity, source, title, body, links, engine, model,
     correlation_id, metadata, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb, $15, $16,
        $17, $18::jsonb, clock_timestamp())
ON CONFLICT (dedupe_key) DO NOTHING
RETURNING *"""

SELECT_RULES_FOR_OWNERS_SQL = """SELECT * FROM forge_notification_rules
WHERE owner_id = ANY($1::text[]) AND enabled
ORDER BY created_at, id
FOR SHARE"""

INSERT_DELIVERY_SQL = """INSERT INTO forge_notification_deliveries
    (id, notification_id, rule_id, sink)
VALUES ($1, $2, $3, $4)
ON CONFLICT (notification_id, rule_id) DO NOTHING"""

CLAIM_DUE_SQL = """WITH due AS (
    SELECT id FROM forge_notification_deliveries
    WHERE status IN ('pending', 'failed', 'claimed') AND next_attempt_at <= now()
    ORDER BY next_attempt_at, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT $1
)
UPDATE forge_notification_deliveries d
SET status = 'claimed',
    attempts = d.attempts + 1,
    lease_until = now() + make_interval(secs => $2::double precision),
    next_attempt_at = now() + make_interval(secs => $2::double precision),
    updated_at = now()
FROM due
WHERE d.id = due.id
RETURNING d.*"""

_SETTLE_GUARD = "WHERE id = $1 AND attempts = $2 AND status = 'claimed'"


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def row_to_notification(row: asyncpg.Record | dict) -> Notification:
    return Notification(
        id=row["id"],
        seq=row["seq"],
        dedupe_key=row["dedupe_key"],
        session_id=row["session_id"],
        session_seq=row["session_seq"],
        session_name=row["session_name"],
        owner_id=row["owner_id"],
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        kind=row["kind"],
        severity=row["severity"],
        source=row["source"],
        title=row["title"],
        body=row["body"],
        links=_json(row["links"], []),
        engine=row["engine"],
        model=row["model"],
        correlation_id=row["correlation_id"],
        metadata=_json(row["metadata"], {}),
        created_at=row["created_at"],
    )


def row_to_rule(row: asyncpg.Record | dict) -> NotificationRule:
    return NotificationRule(
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        enabled=row["enabled"],
        match=NotificationRuleMatch.model_validate(_json(row["match"], {})),
        sink=row["sink"],
        integration_connection_id=row["integration_connection_id"],
        config=_json(row["config"], {}),
        quiet_hours=_json(row["quiet_hours"], None),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_delivery(row: asyncpg.Record | dict) -> NotificationDelivery:
    return NotificationDelivery(
        id=row["id"],
        notification_id=row["notification_id"],
        rule_id=row["rule_id"],
        sink=row["sink"],
        status=DeliveryStatus(row["status"]),
        attempts=row["attempts"],
        next_attempt_at=row["next_attempt_at"],
        lease_until=row["lease_until"],
        last_error=row["last_error"],
        delivered_at=row["delivered_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _candidate_args(candidate: NotificationCandidate) -> tuple:
    links = [link.model_dump(mode="json") for link in candidate.links]
    return (
        candidate.id,
        scrub_text(candidate.dedupe_key),
        candidate.session_id,
        candidate.session_seq,
        scrub_text(candidate.session_name),
        scrub_text(candidate.owner_id),
        scrub_text(candidate.tenant_id),
        scrub_text(candidate.project_id),
        candidate.kind.value,
        candidate.severity.value,
        candidate.source.value,
        scrub_text(candidate.title),
        scrub_text(candidate.body),
        dumps_jsonb(links),
        scrub_text(candidate.engine),
        scrub_text(candidate.model),
        scrub_text(candidate.correlation_id),
        dumps_jsonb(candidate.metadata),
    )


def scope_clause(scope: NotificationScope, params: list[Any]) -> str:
    """SQL predicate for a reader's scope, mirroring ``notification_visible_to``."""
    params.append(scope.user_id)
    user = f"${len(params)}"
    if not scope.is_admin:
        return f"owner_id = {user}"
    params.append(scope.tenant_id)
    tenant = f"${len(params)}"
    return f"(owner_id = {user} OR COALESCE(tenant_id, '') IN ('', {tenant}))"


def feed_sql(
    scope: NotificationScope, query: NotificationQuery, *, read_through_seq: int
) -> tuple[str, list[Any]]:
    """Build the parameterized feed query (one extra row detects a further page)."""
    params: list[Any] = []
    conditions = [scope_clause(scope, params)]

    def bind(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if query.kinds:
        conditions.append(f"kind = ANY({bind([k.value for k in query.kinds])}::text[])")
    if query.severities():
        severities = [s.value for s in query.severities()]
        conditions.append(f"severity = ANY({bind(severities)}::text[])")
    if query.sources:
        conditions.append(f"source = ANY({bind([s.value for s in query.sources])}::text[])")
    if query.session_id is not None:
        conditions.append(f"session_id = {bind(query.session_id)}")
    if query.project_id is not None:
        conditions.append(f"project_id = {bind(query.project_id)}")
    if query.unread is not None:
        through = bind(read_through_seq)
        reader = bind(scope.user_id)
        acknowledged = (
            "EXISTS (SELECT 1 FROM forge_notification_reads r "
            f"WHERE r.notification_id = forge_notifications.id AND r.user_id = {reader})"
        )
        if query.unread:
            conditions.append(f"(seq > {through} AND NOT {acknowledged})")
        else:
            conditions.append(f"(seq <= {through} OR {acknowledged})")
    if query.before is not None:
        conditions.append(f"seq < {bind(query.before)}")
    if query.after is not None:
        conditions.append(f"seq > {bind(query.after)}")
    order = "ASC" if query.ascending else "DESC"
    limit = bind(query.limit + 1)
    sql = (
        "SELECT * FROM forge_notifications WHERE "
        + " AND ".join(conditions)
        + f" ORDER BY seq {order} LIMIT {limit}"
    )
    return sql, params


class PostgresNotificationRepository(NotificationRepository):
    """The feed, the projection transaction and per-reader watermarks."""

    def __init__(self, pool: asyncpg.Pool, **_extra: object) -> None:
        self._pool = pool

    async def project(self, candidates: list[NotificationCandidate]) -> list[Notification]:
        if not candidates:
            return []
        try:
            return await self._project(candidates)
        except _UNAVAILABLE_ERRORS as exc:
            raise NotificationStoreUnavailableError(str(exc) or type(exc).__name__) from exc

    async def _project(self, candidates: list[NotificationCandidate]) -> list[Notification]:
        created: list[Notification] = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(FEED_ORDER_LOCK_SQL)
                for candidate in candidates:
                    row = await conn.fetchrow(INSERT_NOTIFICATION_SQL, *_candidate_args(candidate))
                    if row is not None:
                        created.append(row_to_notification(row))
                if created:
                    await self._schedule_deliveries(conn, created)
        return sorted(created, key=lambda notification: notification.seq)

    @staticmethod
    async def _schedule_deliveries(
        conn: asyncpg.Connection, notifications: list[Notification]
    ) -> None:
        owners = sorted({notification.owner_id for notification in notifications})
        rows = await conn.fetch(SELECT_RULES_FOR_OWNERS_SQL, owners)
        rules = [row_to_rule(row) for row in rows]
        args = [
            (uuid4(), notification.id, rule.id, rule.sink)
            for notification in notifications
            for rule in rules_matching(notification, rules)
        ]
        if args:
            await conn.executemany(INSERT_DELIVERY_SQL, args)

    async def get(self, notification_id: UUID) -> Notification | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM forge_notifications WHERE id = $1", notification_id
        )
        if row is None:
            return None
        return row_to_notification(row)

    async def list_feed(
        self,
        scope: NotificationScope,
        query: NotificationQuery,
        *,
        read_through_seq: int,
    ) -> NotificationPage:
        sql, params = feed_sql(scope, query, read_through_seq=read_through_seq)
        rows = await self._pool.fetch(sql, *params)
        items = [row_to_notification(row) for row in rows[: query.limit]]
        more = len(rows) > query.limit
        next_before = items[-1].seq if more and not query.ascending else None
        return NotificationPage(items=items, next_before=next_before)

    async def list_for_session(
        self, session_id: UUID, *, after: int, limit: int
    ) -> list[Notification]:
        rows = await self._pool.fetch(
            """SELECT * FROM forge_notifications
               WHERE session_id = $1 AND seq > $2
               ORDER BY seq ASC LIMIT $3""",
            session_id,
            after,
            limit,
        )
        return [row_to_notification(row) for row in rows]

    async def feed_counts(
        self, scope: NotificationScope, *, read_through_seq: int
    ) -> tuple[int, int]:
        params: list[Any] = []
        clause = scope_clause(scope, params)
        params.append(read_through_seq)
        through = f"${len(params)}"
        params.append(scope.user_id)
        reader = f"${len(params)}"
        row = await self._pool.fetchrow(
            f"""SELECT
                (SELECT COALESCE(MAX(seq), 0) FROM forge_notifications WHERE {clause})
                    AS head_seq,
                (SELECT COUNT(*) FROM forge_notifications WHERE {clause} AND seq > {through}
                 AND NOT EXISTS (SELECT 1 FROM forge_notification_reads r
                     WHERE r.notification_id = forge_notifications.id AND r.user_id = {reader}))
                    AS unread_count""",
            *params,
        )
        return int(row["head_seq"]), int(row["unread_count"])

    async def get_watermark(self, user_id: str) -> ReadWatermark:
        row = await self._pool.fetchrow(
            """SELECT read_through_seq, revision FROM forge_notification_read_states
               WHERE user_id = $1""",
            user_id,
        )
        if row is None:
            return ReadWatermark()
        return ReadWatermark(read_through_seq=row["read_through_seq"], revision=row["revision"])

    async def advance_watermark(
        self, user_id: str, *, read_through_seq: int, expected_revision: int
    ) -> ReadWatermark:
        if expected_revision == 0:
            # First write for this reader. A concurrent first write that already
            # inserted the row has revision >= 1, so the guarded upsert refuses it.
            row = await self._pool.fetchrow(
                """INSERT INTO forge_notification_read_states
                       (user_id, read_through_seq, revision, updated_at)
                   VALUES ($1, $2, 1, now())
                   ON CONFLICT (user_id) DO UPDATE
                   SET read_through_seq = GREATEST(
                           forge_notification_read_states.read_through_seq,
                           EXCLUDED.read_through_seq),
                       revision = forge_notification_read_states.revision + 1,
                       updated_at = now()
                   WHERE forge_notification_read_states.revision = 0
                   RETURNING read_through_seq, revision""",
                user_id,
                read_through_seq,
            )
        else:
            row = await self._pool.fetchrow(
                """UPDATE forge_notification_read_states
                   SET read_through_seq = GREATEST(read_through_seq, $2),
                       revision = revision + 1,
                       updated_at = now()
                   WHERE user_id = $1 AND revision = $3
                   RETURNING read_through_seq, revision""",
                user_id,
                read_through_seq,
                expected_revision,
            )
        if row is None:
            raise NotificationReadStateConflictError(
                "Notification read state changed; refresh before changing it"
            )
        return ReadWatermark(read_through_seq=row["read_through_seq"], revision=row["revision"])

    async def read_ids(self, user_id: str, notification_ids: list[UUID]) -> set[UUID]:
        if not notification_ids:
            return set()
        rows = await self._pool.fetch(
            "SELECT notification_id FROM forge_notification_reads "
            "WHERE user_id = $1 AND notification_id = ANY($2::uuid[])",
            user_id,
            notification_ids,
        )
        return {row["notification_id"] for row in rows}

    async def mark_read(self, user_id: str, notification_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO forge_notification_read_states (user_id) VALUES ($1) "
                    "ON CONFLICT (user_id) DO NOTHING",
                    user_id,
                )
                # Serialize with watermark CAS and other item acknowledgements. A retry
                # inserts no second mark and changes neither revision nor unread count.
                await conn.fetchrow(
                    "SELECT revision FROM forge_notification_read_states "
                    "WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                try:
                    inserted = await conn.fetchrow(
                        "INSERT INTO forge_notification_reads (user_id, notification_id) "
                        "VALUES ($1, $2) ON CONFLICT DO NOTHING RETURNING notification_id",
                        user_id,
                        notification_id,
                    )
                except asyncpg.exceptions.ForeignKeyViolationError as exc:
                    raise NotificationNotFoundError(
                        f"Notification not found: {notification_id}"
                    ) from exc
                if inserted is not None:
                    await conn.execute(
                        "UPDATE forge_notification_read_states SET revision = revision + 1, "
                        "updated_at = now() WHERE user_id = $1",
                        user_id,
                    )


def _rule_args(rule: NotificationRule) -> tuple:
    return (
        rule.id,
        rule.owner_id,
        scrub_text(rule.name),
        rule.enabled,
        dumps_jsonb(rule.match.model_dump(mode="json")),
        rule.sink,
        rule.integration_connection_id,
        dumps_jsonb(rule.config),
        dumps_jsonb(rule.quiet_hours.model_dump(mode="json")) if rule.quiet_hours else None,
    )


class PostgresNotificationRuleRepository(NotificationRuleRepository):
    """Owner-scoped delivery rules."""

    def __init__(self, pool: asyncpg.Pool, **_extra: object) -> None:
        self._pool = pool

    async def list_for_owner(self, owner_id: str) -> list[NotificationRule]:
        rows = await self._pool.fetch(
            """SELECT * FROM forge_notification_rules
               WHERE owner_id = $1 ORDER BY created_at, id""",
            owner_id,
        )
        return [row_to_rule(row) for row in rows]

    async def list_enabled_for_owner(self, owner_id: str) -> list[NotificationRule]:
        rows = await self._pool.fetch(
            """SELECT * FROM forge_notification_rules
               WHERE owner_id = $1 AND enabled ORDER BY created_at, id""",
            owner_id,
        )
        return [row_to_rule(row) for row in rows]

    async def get(self, owner_id: str, rule_id: UUID) -> NotificationRule | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM forge_notification_rules WHERE id = $1 AND owner_id = $2",
            rule_id,
            owner_id,
        )
        if row is None:
            return None
        return row_to_rule(row)

    async def create(self, rule: NotificationRule) -> NotificationRule:
        row = await self._pool.fetchrow(
            """INSERT INTO forge_notification_rules
                   (id, owner_id, name, enabled, match, sink, integration_connection_id,
                    config, quiet_hours, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::jsonb, $9::jsonb, $10, $11)
               RETURNING *""",
            *_rule_args(rule),
            rule.created_at,
            rule.updated_at,
        )
        return row_to_rule(row)

    async def create_if_absent(self, rule: NotificationRule) -> NotificationRule | None:
        row = await self._pool.fetchrow(
            """INSERT INTO forge_notification_rules
                   (id, owner_id, name, enabled, match, sink, integration_connection_id,
                    config, quiet_hours, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::jsonb, $9::jsonb, $10, $11)
               ON CONFLICT (id) DO NOTHING
               RETURNING *""",
            *_rule_args(rule),
            rule.created_at,
            rule.updated_at,
        )
        if row is None:
            return None
        return row_to_rule(row)

    async def update(self, rule: NotificationRule) -> NotificationRule | None:
        row = await self._pool.fetchrow(
            """UPDATE forge_notification_rules
               SET name = $3, enabled = $4, match = $5::jsonb, sink = $6,
                   integration_connection_id = $7, config = $8::jsonb,
                   quiet_hours = $9::jsonb, updated_at = $10
               WHERE id = $1 AND owner_id = $2
               RETURNING *""",
            *_rule_args(rule),
            rule.updated_at,
        )
        if row is None:
            return None
        return row_to_rule(row)

    async def delete(self, owner_id: str, rule_id: UUID) -> bool:
        result = await self._pool.execute(
            "DELETE FROM forge_notification_rules WHERE id = $1 AND owner_id = $2",
            rule_id,
            owner_id,
        )
        return result == "DELETE 1"


class PostgresNotificationDeliveryRepository(NotificationDeliveryRepository):
    """The delivery outbox: lease-based claims and fenced settles."""

    def __init__(self, pool: asyncpg.Pool, *, max_error_chars: int, **_extra: object) -> None:
        if max_error_chars < 1:
            raise ValueError("max_error_chars must be at least 1")
        self._pool = pool
        self._max_error_chars = max_error_chars

    async def claim_due(self, *, limit: int, lease_seconds: float) -> list[ClaimedDelivery]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(CLAIM_DUE_SQL, limit, float(lease_seconds))
                if not rows:
                    return []
                deliveries = [row_to_delivery(row) for row in rows]
                notification_rows = await conn.fetch(
                    "SELECT * FROM forge_notifications WHERE id = ANY($1::uuid[])",
                    [delivery.notification_id for delivery in deliveries],
                )
                rule_rows = await conn.fetch(
                    "SELECT * FROM forge_notification_rules WHERE id = ANY($1::uuid[])",
                    [delivery.rule_id for delivery in deliveries],
                )
        notifications = {row["id"]: row_to_notification(row) for row in notification_rows}
        rules = {row["id"]: row_to_rule(row) for row in rule_rows}
        # FKs cascade on delete and the claim held row locks, so every claimed row's
        # notification and rule exist in the same snapshot.
        claimed = [
            ClaimedDelivery(
                delivery=delivery,
                notification=notifications[delivery.notification_id],
                rule=rules[delivery.rule_id],
            )
            for delivery in deliveries
        ]
        claimed.sort(key=lambda item: (item.delivery.created_at, item.notification.seq))
        return claimed

    async def mark_delivered(self, delivery: NotificationDelivery) -> bool:
        result = await self._pool.execute(
            f"""UPDATE forge_notification_deliveries
                SET status = 'delivered', delivered_at = now(), lease_until = NULL,
                    last_error = NULL, updated_at = now()
                {_SETTLE_GUARD}""",
            delivery.id,
            delivery.attempts,
        )
        return result == "UPDATE 1"

    async def mark_failed(
        self, delivery: NotificationDelivery, *, error: str, retry_at: datetime | None
    ) -> bool:
        status = DeliveryStatus.FAILED if retry_at is not None else DeliveryStatus.DEAD
        result = await self._pool.execute(
            f"""UPDATE forge_notification_deliveries
                SET status = $3, next_attempt_at = COALESCE($4, next_attempt_at),
                    lease_until = NULL, last_error = $5, updated_at = now()
                {_SETTLE_GUARD}""",
            delivery.id,
            delivery.attempts,
            status.value,
            retry_at,
            scrub_text(error[: self._max_error_chars]),
        )
        return result == "UPDATE 1"

    async def mark_suppressed(self, delivery: NotificationDelivery, *, reason: str) -> bool:
        result = await self._pool.execute(
            f"""UPDATE forge_notification_deliveries
                SET status = 'suppressed', lease_until = NULL, last_error = $3,
                    updated_at = now()
                {_SETTLE_GUARD}""",
            delivery.id,
            delivery.attempts,
            scrub_text(reason[: self._max_error_chars]),
        )
        return result == "UPDATE 1"

    async def list_for_notification(self, notification_id: UUID) -> list[NotificationDelivery]:
        rows = await self._pool.fetch(
            """SELECT * FROM forge_notification_deliveries
               WHERE notification_id = $1 ORDER BY created_at, id""",
            notification_id,
        )
        return [row_to_delivery(row) for row in rows]

    async def count_delivered_since(self, rule_id: UUID, since: datetime) -> int:
        count = await self._pool.fetchval(
            """SELECT COUNT(*) FROM forge_notification_deliveries
               WHERE rule_id = $1 AND status = 'delivered' AND delivered_at >= $2""",
            rule_id,
            since,
        )
        return int(count or 0)
