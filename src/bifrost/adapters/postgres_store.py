"""PostgreSQL-backed UsageStore adapter.

Uses ``asyncpg`` for async I/O. Suitable for multi-node deployments where the
usage data must be shared across gateway instances.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import asyncpg

from bifrost.ports.usage_store import (
    TimeSeriesEntry,
    UsageRecord,
    UsageStore,
    UsageSummary,
)

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS usage_records (
    id                 BIGSERIAL PRIMARY KEY,
    request_id         TEXT      NOT NULL DEFAULT '',
    agent_id           TEXT      NOT NULL,
    tenant_id          TEXT      NOT NULL,
    session_id         TEXT      NOT NULL DEFAULT '',
    saga_id            TEXT      NOT NULL DEFAULT '',
    model              TEXT      NOT NULL,
    provider           TEXT      NOT NULL DEFAULT '',
    input_tokens       INTEGER   NOT NULL DEFAULT 0,
    output_tokens      INTEGER   NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER   NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER   NOT NULL DEFAULT 0,
    reasoning_tokens   INTEGER   NOT NULL DEFAULT 0,
    cost_usd           NUMERIC   NOT NULL DEFAULT 0,
    latency_ms         REAL      NOT NULL DEFAULT 0,
    streaming          BOOLEAN   NOT NULL DEFAULT FALSE,
    cache_hit          BOOLEAN   NOT NULL DEFAULT FALSE,
    timestamp          TIMESTAMPTZ NOT NULL
);
"""

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_usage_tenant_ts  ON usage_records(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_agent_ts   ON usage_records(agent_id,  timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_model      ON usage_records(model);
CREATE INDEX IF NOT EXISTS idx_usage_provider   ON usage_records(provider);
"""

# Additive migrations for databases created before NIU-486.
_MIGRATE_COLUMNS = [
    "ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS cache_hit BOOLEAN NOT NULL DEFAULT FALSE",
]

_INSERT = """
INSERT INTO usage_records
    (request_id, agent_id, tenant_id, session_id, saga_id,
     model, provider,
     input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens,
     cost_usd, latency_ms, streaming, cache_hit, timestamp)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
"""


def _ts(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def _record_from_row(row: asyncpg.Record) -> UsageRecord:
    return UsageRecord(
        request_id=row["request_id"],
        agent_id=row["agent_id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        saga_id=row["saga_id"],
        model=row["model"],
        provider=row["provider"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cache_read_tokens=row["cache_read_tokens"],
        cache_write_tokens=row["cache_write_tokens"],
        reasoning_tokens=row["reasoning_tokens"],
        cost_usd=float(row["cost_usd"]),
        latency_ms=row["latency_ms"],
        streaming=row["streaming"],
        cache_hit=row["cache_hit"],
        timestamp=row["timestamp"].replace(tzinfo=UTC),
    )


def _build_where(
    agent_id: str | None,
    tenant_id: str | None,
    model: str | None,
    since: datetime | None,
    until: datetime | None,
    start_idx: int = 1,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    idx = start_idx

    if agent_id is not None:
        clauses.append(f"agent_id = ${idx}")
        params.append(agent_id)
        idx += 1
    if tenant_id is not None:
        clauses.append(f"tenant_id = ${idx}")
        params.append(tenant_id)
        idx += 1
    if model is not None:
        clauses.append(f"model = ${idx}")
        params.append(model)
        idx += 1
    if since is not None:
        clauses.append(f"timestamp >= ${idx}")
        params.append(_ts(since))
        idx += 1
    if until is not None:
        clauses.append(f"timestamp <= ${idx}")
        params.append(_ts(until))

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


class PostgresUsageStore(UsageStore):
    """asyncpg-based PostgreSQL implementation of ``UsageStore``.

    Args:
        dsn: PostgreSQL connection string, e.g.
             ``postgresql://user:pass@host/dbname``.
        min_size: Minimum connection pool size.
        max_size: Maximum connection pool size.
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
            )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    async def record(self, usage: UsageRecord) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _INSERT,
                usage.request_id,
                usage.agent_id,
                usage.tenant_id,
                usage.session_id,
                usage.saga_id,
                usage.model,
                usage.provider,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
                usage.reasoning_tokens,
                usage.cost_usd,
                usage.latency_ms,
                usage.streaming,
                usage.cache_hit,
                _ts(usage.timestamp),
            )

    async def query(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        model: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
    ) -> list[UsageRecord]:
        where, params = _build_where(agent_id, tenant_id, model, since, until, start_idx=1)
        limit_idx = len(params) + 1
        sql = f"""
            SELECT request_id, agent_id, tenant_id, session_id, saga_id,
                   model, provider,
                   input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                   reasoning_tokens, cost_usd, latency_ms, streaming, cache_hit, timestamp
            FROM usage_records {where}
            ORDER BY timestamp DESC
            LIMIT ${limit_idx}
        """
        params.append(limit)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_record_from_row(r) for r in rows]

    async def summarise(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        model: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> UsageSummary:
        where, params = _build_where(agent_id, tenant_id, model, since, until, start_idx=1)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            totals_row = await conn.fetchrow(
                f"SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) "
                f"FROM usage_records {where}",
                *params,
            )
            model_rows = await conn.fetch(
                f"SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) "
                f"FROM usage_records {where} GROUP BY model",
                *params,
            )
            provider_rows = await conn.fetch(
                f"SELECT provider, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) "
                f"FROM usage_records {where} GROUP BY provider",
                *params,
            )

        summary = UsageSummary(
            total_requests=totals_row[0] or 0,
            total_input_tokens=totals_row[1] or 0,
            total_output_tokens=totals_row[2] or 0,
            total_cost_usd=float(totals_row[3] or 0),
        )
        for row in model_rows:
            summary.by_model[row[0]] = {
                "requests": row[1] or 0,
                "input_tokens": row[2] or 0,
                "output_tokens": row[3] or 0,
                "cost_usd": float(row[4] or 0),
            }
        for row in provider_rows:
            summary.by_provider[row[0] or "unknown"] = {
                "requests": row[1] or 0,
                "input_tokens": row[2] or 0,
                "output_tokens": row[3] or 0,
                "cost_usd": float(row[4] or 0),
            }
        return summary

    async def time_series(
        self,
        *,
        granularity: str = "hour",
        agent_id: str | None = None,
        tenant_id: str | None = None,
        model: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[TimeSeriesEntry]:
        where, params = _build_where(agent_id, tenant_id, model, since, until, start_idx=1)
        trunc = "hour" if granularity != "day" else "day"
        sql = f"""
            SELECT
                DATE_TRUNC('{trunc}', timestamp) AS bucket,
                COUNT(*) AS requests,
                SUM(input_tokens) AS input_tokens,
                SUM(output_tokens) AS output_tokens,
                SUM(cost_usd) AS cost_usd
            FROM usage_records {where}
            GROUP BY bucket
            ORDER BY bucket ASC
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [
            TimeSeriesEntry(
                bucket=row["bucket"].replace(tzinfo=UTC).isoformat(),
                requests=row["requests"] or 0,
                input_tokens=row["input_tokens"] or 0,
                output_tokens=row["output_tokens"] or 0,
                cost_usd=float(row["cost_usd"] or 0),
            )
            for row in rows
        ]

    async def tokens_today(self, tenant_id: str) -> int:
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT SUM(input_tokens + output_tokens) FROM usage_records "
                "WHERE tenant_id = $1 AND timestamp >= $2",
                tenant_id,
                since,
            )
        return int(row[0] or 0)

    async def cost_today(self, tenant_id: str) -> float:
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT SUM(cost_usd) FROM usage_records WHERE tenant_id = $1 AND timestamp >= $2",
                tenant_id,
                since,
            )
        return float(row[0] or 0)

    async def requests_this_hour(self, tenant_id: str) -> int:
        since = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) FROM usage_records WHERE tenant_id = $1 AND timestamp >= $2",
                tenant_id,
                since,
            )
        return int(row[0] or 0)

    async def agent_cost_today(self, agent_id: str) -> float:
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT SUM(cost_usd) FROM usage_records WHERE agent_id = $1 AND timestamp >= $2",
                agent_id,
                since,
            )
        return float(row[0] or 0)
