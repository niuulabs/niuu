"""Tests for the pass-through methods of LazyPostgresTriggerStore and
LazyPostgresBudgetLedger — previously only their construction (isinstance)
was covered, never that the delegate methods actually delegate.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ravn.adapters.budget_ledger.postgres_store import (
    LazyPostgresBudgetLedger,
    PostgresBudgetLedger,
)
from ravn.adapters.trigger_store.postgres_store import (
    LazyPostgresTriggerStore,
    PostgresTriggerStore,
)
from ravn.domain.budget_totals import EMPTY_BUDGET_TOTALS, BudgetTotals


def _lazy_trigger_store(pool: AsyncMock) -> LazyPostgresTriggerStore:
    store = LazyPostgresTriggerStore(dsn="postgresql://example/db")
    store._lazy = SimpleNamespace(resolve=AsyncMock(return_value=pool))  # noqa: SLF001
    return store


def _lazy_budget_ledger(pool: AsyncMock) -> LazyPostgresBudgetLedger:
    ledger = LazyPostgresBudgetLedger(dsn="postgresql://example/db")
    ledger._lazy = SimpleNamespace(resolve=AsyncMock(return_value=pool))  # noqa: SLF001
    return ledger


_TRIGGER_ROW = {
    "id": "t-1",
    "kind": "cron",
    "persona_name": "p",
    "spec": "0 * * * *",
    "repo": "",
    "enabled": True,
    "owner_id": "u-1",
    "tenant_id": "tenant-a",
    "created_at": "2026-01-01T00:00:00Z",
}


class TestLazyPostgresTriggerStorePassThrough:
    async def test_list_triggers_delegates_to_the_resolved_pool(self) -> None:
        pool = AsyncMock()
        pool.fetch.return_value = [_TRIGGER_ROW]
        store = _lazy_trigger_store(pool)

        result = await store.list_triggers(tenant_id="tenant-a")

        assert len(result) == 1
        assert result[0].id == "t-1"
        pool.fetch.assert_awaited_once()
        assert pool.fetch.call_args.args[1] == "tenant-a"

    async def test_get_trigger_delegates_to_the_resolved_pool(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = _TRIGGER_ROW
        store = _lazy_trigger_store(pool)

        result = await store.get_trigger("t-1")

        assert result is not None
        assert result.id == "t-1"
        pool.fetchrow.assert_awaited_once()

    async def test_get_trigger_returns_none_when_the_pool_finds_nothing(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = None
        store = _lazy_trigger_store(pool)

        assert await store.get_trigger("missing") is None

    async def test_create_trigger_delegates_to_the_resolved_pool(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = _TRIGGER_ROW
        store = _lazy_trigger_store(pool)

        result = await store.create_trigger(
            kind="cron",
            persona_name="p",
            spec="0 * * * *",
            enabled=True,
            owner_id="u-1",
            tenant_id="tenant-a",
        )

        assert result.id == "t-1"
        pool.fetchrow.assert_awaited_once()

    async def test_delete_trigger_delegates_and_reports_success(self) -> None:
        pool = AsyncMock()
        pool.execute.return_value = "DELETE 1"
        store = _lazy_trigger_store(pool)

        assert await store.delete_trigger("t-1") is True

    async def test_delete_trigger_reports_failure_when_nothing_matched(self) -> None:
        pool = AsyncMock()
        pool.execute.return_value = "DELETE 0"
        store = _lazy_trigger_store(pool)

        assert await store.delete_trigger("missing") is False

    async def test_two_calls_resolve_the_pool_independently_each_time(self) -> None:
        """_delegate() is called fresh per method — resolve() itself is what
        caches the underlying pool (see _lazy_asyncpg_pool), so this just
        proves every pass-through method actually calls through resolve()."""
        pool = AsyncMock()
        pool.fetch.return_value = []
        store = _lazy_trigger_store(pool)

        await store.list_triggers(tenant_id="t")
        await store.list_triggers(tenant_id="t")

        assert store._lazy.resolve.await_count == 2  # noqa: SLF001

    def test_empty_dsn_raises_immediately(self) -> None:
        with pytest.raises(ValueError, match="non-empty dsn"):
            LazyPostgresTriggerStore(dsn="")

    def test_whitespace_only_dsn_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty dsn"):
            LazyPostgresTriggerStore(dsn="   ")

    async def test_warmup_connects_the_pool(self) -> None:
        pool = AsyncMock()
        store = _lazy_trigger_store(pool)

        await store.warmup()

        store._lazy.resolve.assert_awaited_once()  # noqa: SLF001


class TestPostgresTriggerStoreDirect:
    """The underlying (non-lazy) store the Lazy* wrapper delegates to."""

    async def test_list_triggers_maps_rows_to_records(self) -> None:
        pool = AsyncMock()
        pool.fetch.return_value = [_TRIGGER_ROW]
        store = PostgresTriggerStore(pool)

        result = await store.list_triggers(tenant_id="tenant-a")

        assert result[0].kind == "cron"
        assert result[0].repo == ""


_LEDGER_ROW = {"spent_usd": 0.42, "cap_usd": 1.0, "warn_at": 0.8}


class TestLazyPostgresBudgetLedgerPassThrough:
    async def test_record_spend_delegates_to_the_resolved_pool(self) -> None:
        pool = AsyncMock()
        ledger = _lazy_budget_ledger(pool)

        await ledger.record_spend(
            "ravn-1",
            tenant_id="tenant-a",
            day=date(2026, 1, 1),
            cost_usd=0.1,
            cap_usd=1.0,
            warn_at=0.8,
        )

        pool.execute.assert_awaited_once()

    async def test_totals_for_delegates_and_maps_the_row(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = _LEDGER_ROW
        ledger = _lazy_budget_ledger(pool)

        totals = await ledger.totals_for("ravn-1", tenant_id="tenant-a", day=date(2026, 1, 1))

        assert totals == BudgetTotals(spent_usd=0.42, cap_usd=1.0, warn_at=0.8)

    async def test_totals_for_returns_empty_when_the_pool_finds_nothing(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = None
        ledger = _lazy_budget_ledger(pool)

        totals = await ledger.totals_for("ravn-1", tenant_id="tenant-a", day=date(2026, 1, 1))

        assert totals == EMPTY_BUDGET_TOTALS

    async def test_fleet_totals_delegates_and_maps_the_row(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = {"spent_usd": 3.0, "cap_usd": 10.0, "warn_at": 0.8}
        ledger = _lazy_budget_ledger(pool)

        totals = await ledger.fleet_totals(day=date(2026, 1, 1), tenant_id="tenant-a")

        assert totals == BudgetTotals(spent_usd=3.0, cap_usd=10.0, warn_at=0.8)

    async def test_fleet_totals_returns_empty_when_both_sums_are_zero(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = {"spent_usd": 0, "cap_usd": 0, "warn_at": 0}
        ledger = _lazy_budget_ledger(pool)

        totals = await ledger.fleet_totals(day=date(2026, 1, 1), tenant_id="tenant-a")

        assert totals == EMPTY_BUDGET_TOTALS

    def test_empty_dsn_raises_immediately(self) -> None:
        with pytest.raises(ValueError, match="non-empty dsn"):
            LazyPostgresBudgetLedger(dsn="")

    async def test_warmup_connects_the_pool(self) -> None:
        pool = AsyncMock()
        ledger = _lazy_budget_ledger(pool)

        await ledger.warmup()

        ledger._lazy.resolve.assert_awaited_once()  # noqa: SLF001


class TestPostgresBudgetLedgerDirect:
    async def test_totals_for_maps_the_row(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = _LEDGER_ROW
        ledger = PostgresBudgetLedger(pool)

        totals = await ledger.totals_for("ravn-1", tenant_id="tenant-a", day=date(2026, 1, 1))

        assert totals == BudgetTotals(spent_usd=0.42, cap_usd=1.0, warn_at=0.8)
