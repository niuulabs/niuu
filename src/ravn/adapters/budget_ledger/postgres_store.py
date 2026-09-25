"""PostgreSQL-backed budget ledger (raw SQL, asyncpg)."""

from __future__ import annotations

from datetime import date
from typing import Any

from ravn.adapters._lazy_asyncpg_pool import lazy_pool_for_dsn
from ravn.domain.budget_totals import EMPTY_BUDGET_TOTALS, BudgetTotals
from ravn.ports.budget_ledger import BudgetLedgerPort

_UPSERT_SQL = """
INSERT INTO ravn_budget_ledger (tenant_id, ravn_id, day, spent_usd, cap_usd, warn_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, NOW())
ON CONFLICT (tenant_id, ravn_id, day) DO UPDATE SET
    spent_usd = ravn_budget_ledger.spent_usd + EXCLUDED.spent_usd,
    cap_usd = EXCLUDED.cap_usd,
    warn_at = EXCLUDED.warn_at,
    updated_at = NOW()
"""


class PostgresBudgetLedger(BudgetLedgerPort):
    """Store budget totals in ``ravn_budget_ledger``, one row per ravn per day."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def record_spend(
        self,
        ravn_id: str,
        *,
        tenant_id: str,
        day: date,
        cost_usd: float,
        cap_usd: float,
        warn_at: float,
    ) -> None:
        await self._pool.execute(_UPSERT_SQL, tenant_id, ravn_id, day, cost_usd, cap_usd, warn_at)

    async def totals_for(self, ravn_id: str, *, tenant_id: str, day: date) -> BudgetTotals:
        row = await self._pool.fetchrow(
            "SELECT spent_usd, cap_usd, warn_at FROM ravn_budget_ledger "
            "WHERE ravn_id = $1 AND tenant_id = $2 AND day = $3",
            ravn_id,
            tenant_id,
            day,
        )
        return _totals_from_row(row)

    async def fleet_totals(self, *, day: date, tenant_id: str) -> BudgetTotals:
        row = await self._pool.fetchrow(
            "SELECT COALESCE(SUM(spent_usd), 0) AS spent_usd, "
            "COALESCE(SUM(cap_usd), 0) AS cap_usd, "
            "COALESCE(MIN(warn_at), 0) AS warn_at "
            "FROM ravn_budget_ledger WHERE day = $1 AND tenant_id = $2",
            day,
            tenant_id,
        )
        if row is None or (row["spent_usd"] == 0 and row["cap_usd"] == 0):
            return EMPTY_BUDGET_TOTALS
        return _totals_from_row(row)


class LazyPostgresBudgetLedger(BudgetLedgerPort):
    """Dynamic-adapter entry point: ``adapter:`` + ``kwargs: {dsn: ...}``.

    Connection is deferred (constructing this adapter never blocks) but
    callers should ``await warmup()`` once at process startup (see
    ``ravn.api.persistence_wiring``) rather than let the first real request
    discover a bad DSN. Shares its pool with any other Ravn store configured
    with the same DSN (see ``ravn.adapters._lazy_asyncpg_pool``).
    """

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError(
                "LazyPostgresBudgetLedger requires a non-empty dsn — asyncpg would "
                "otherwise silently fall back to libpq environment defaults instead "
                "of the Postgres this deployment actually configured"
            )
        self._lazy = lazy_pool_for_dsn(dsn)

    async def warmup(self) -> None:
        """Connect now, so a bad DSN fails at startup, not on first request."""
        await self._lazy.resolve()

    async def _delegate(self) -> PostgresBudgetLedger:
        return PostgresBudgetLedger(await self._lazy.resolve())

    async def record_spend(self, ravn_id: str, **kwargs: Any) -> None:
        await (await self._delegate()).record_spend(ravn_id, **kwargs)

    async def totals_for(self, ravn_id: str, *, tenant_id: str, day: date) -> BudgetTotals:
        return await (await self._delegate()).totals_for(ravn_id, tenant_id=tenant_id, day=day)

    async def fleet_totals(self, *, day: date, tenant_id: str) -> BudgetTotals:
        return await (await self._delegate()).fleet_totals(day=day, tenant_id=tenant_id)


def _totals_from_row(row: Any) -> BudgetTotals:
    if row is None:
        return EMPTY_BUDGET_TOTALS
    return BudgetTotals(
        spent_usd=float(row["spent_usd"]),
        cap_usd=float(row["cap_usd"]),
        warn_at=float(row["warn_at"]),
    )
