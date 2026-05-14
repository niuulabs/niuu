"""PostgreSQL audit repository adapter for Sleipnir (infra / multi-node mode).

Uses ``asyncpg`` with raw SQL and a connection pool.  The table is partitioned
by month at the database level (see the matching migration).

JSONB is used for the ``payload`` column so operators can run ad-hoc JSON
queries directly on the audit log without unpacking application-level blobs.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from datetime import UTC, datetime

import asyncpg

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.audit import AuditQuery, AuditRepository

logger = logging.getLogger(__name__)

_INSERT = """
INSERT INTO sleipnir_events
    (event_id, event_type, source, summary, urgency, domain,
     correlation_id, causation_id, tenant_id, payload, timestamp, ttl)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (event_id) DO NOTHING
"""

_PURGE = """
DELETE FROM sleipnir_events
WHERE ttl IS NOT NULL
  AND timestamp + (ttl * INTERVAL '1 second') < NOW()
"""


class PostgresAuditRepository(AuditRepository):
    """PostgreSQL-backed audit repository using an asyncpg connection pool.

    :param pool: An open asyncpg connection pool pointed at a database that
        has the ``sleipnir_events`` table created by the matching migration.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # AuditRepository interface
    # ------------------------------------------------------------------

    async def append(self, event: SleipnirEvent) -> None:
        payload_json = json.dumps(event.payload)
        await self._pool.execute(
            _INSERT,
            event.event_id,
            event.event_type,
            event.source,
            event.summary,
            event.urgency,
            event.domain,
            event.correlation_id,
            event.causation_id,
            event.tenant_id,
            payload_json,
            event.timestamp,
            event.ttl,
        )

    async def query(self, q: AuditQuery) -> list[SleipnirEvent]:
        sql, params = _build_query(q)
        rows = await self._pool.fetch(sql, *params)
        events = [_row_to_event(row) for row in rows]
        events = apply_event_type_filter(events, q.event_type_pattern)
        return events[: q.limit]

    async def purge_expired(self) -> int:
        result: str = await self._pool.execute(_PURGE)
        # asyncpg returns e.g. "DELETE 42"
        try:
            count = int(result.split()[-1])
        except (IndexError, ValueError):
            count = 0
        logger.debug("Postgres audit purge removed %d expired rows", count)
        return count


# ------------------------------------------------------------------
# Query builder
# ------------------------------------------------------------------


def _build_query(q: AuditQuery) -> tuple[str, list]:
    """Construct a parameterised SELECT for *q*.

    Pattern matching is handled by the caller when the pattern contains glob
    characters, because PostgreSQL ``LIKE`` uses ``%`` / ``_`` as wildcards
    while Sleipnir uses ``*`` / ``?``.  We convert simple trailing-star
    patterns (``ravn.*``) to LIKE predicates; for more complex patterns we
    fall back to application-level fnmatch filtering after a broader fetch.
    """
    conditions: list[str] = []
    params: list = []
    idx = 1

    apply_fnmatch = False

    if q.event_type_pattern and q.event_type_pattern != "*":
        if _is_simple_prefix_pattern(q.event_type_pattern):
            # Convert "ravn.*" → LIKE 'ravn.%'
            like_val = q.event_type_pattern.rstrip("*") + "%"
            conditions.append(f"event_type LIKE ${idx}")
            params.append(like_val)
            idx += 1
        else:
            # Complex pattern — fetch broadly, filter in Python
            apply_fnmatch = True

    if q.from_ts is not None:
        conditions.append(f"timestamp >= ${idx}")
        params.append(q.from_ts)
        idx += 1
    if q.to_ts is not None:
        conditions.append(f"timestamp <= ${idx}")
        params.append(q.to_ts)
        idx += 1
    if q.correlation_id is not None:
        conditions.append(f"correlation_id = ${idx}")
        params.append(q.correlation_id)
        idx += 1
    if q.source is not None:
        conditions.append(f"source = ${idx}")
        params.append(q.source)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    # Over-fetch when we need Python-level fnmatch filtering
    limit = q.limit * 10 if apply_fnmatch else q.limit
    sql = f"""
        SELECT event_id, event_type, source, summary, urgency, domain,
               correlation_id, causation_id, tenant_id, payload, timestamp, ttl
        FROM sleipnir_events
        {where}
        ORDER BY timestamp DESC
        LIMIT ${idx}
    """
    params.append(limit)

    if apply_fnmatch:
        # Wrap in a lambda so the caller can post-filter; we embed the pattern
        # in the SQL comment for observability but do nothing special here.
        pass

    return sql, params


def _is_simple_prefix_pattern(pattern: str) -> bool:
    """Return True if *pattern* is a simple ``prefix.*`` style glob.

    Examples that qualify: ``"ravn.*"``, ``"ting.task.*"``.
    Examples that do NOT: ``"*"`` (bare wildcard), ``"ravn.*.complete"``,
    ``"ravn.?ool.*"``.
    """
    if not pattern.endswith("*"):
        return False
    prefix = pattern[:-1]
    # Bare "*" is not a prefix pattern; require a non-empty prefix.
    if not prefix:
        return False
    return "*" not in prefix and "?" not in prefix and "[" not in prefix


# ------------------------------------------------------------------
# Row → domain model
# ------------------------------------------------------------------


def _row_to_event(row: asyncpg.Record) -> SleipnirEvent:
    ts: datetime = row["timestamp"]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    payload_raw = row["payload"]
    if isinstance(payload_raw, str):
        payload: dict = json.loads(payload_raw)
    elif payload_raw is None:
        payload = {}
    else:
        payload = dict(payload_raw)

    return SleipnirEvent(
        event_id=row["event_id"],
        event_type=row["event_type"],
        source=row["source"],
        summary=row["summary"] or "",
        urgency=row["urgency"] if row["urgency"] is not None else 0.0,
        domain=row["domain"] or "code",
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        tenant_id=row["tenant_id"],
        payload=payload,
        timestamp=ts,
        ttl=row["ttl"],
    )


def apply_event_type_filter(
    events: list[SleipnirEvent], pattern: str | None
) -> list[SleipnirEvent]:
    """Post-filter *events* by *pattern* using fnmatch.

    Used when the pattern cannot be expressed as a simple LIKE predicate.
    """
    if not pattern or pattern == "*":
        return events
    return [e for e in events if fnmatch.fnmatch(e.event_type, pattern)]
