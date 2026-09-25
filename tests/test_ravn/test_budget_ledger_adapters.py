"""Tests for ravn.adapters.budget_ledger (file + postgres)."""

from __future__ import annotations

from datetime import date

import pytest

from ravn.adapters.budget_ledger.file_store import FileBudgetLedger
from ravn.adapters.budget_ledger.postgres_store import PostgresBudgetLedger

_DAY = date(2026, 9, 24)


class FakeAsyncpgPool:
    """Minimal fake asyncpg pool implementing the upsert this adapter needs.

    Keyed by ``(tenant_id, ravn_id, day)`` — matching the real table's
    primary key — so two tenants recording under the same ravn_id on the
    same day land in two distinct rows, not one shared/overwritten row.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, date], dict] = {}

    async def execute(self, query: str, *args):
        del query
        tenant_id, ravn_id, day, cost_usd, cap_usd, warn_at = args
        key = (tenant_id, ravn_id, day)
        existing = self.rows.get(key, {"spent_usd": 0.0})
        self.rows[key] = {
            "ravn_id": ravn_id,
            "tenant_id": tenant_id,
            "day": day,
            "spent_usd": existing["spent_usd"] + cost_usd,
            "cap_usd": cap_usd,
            "warn_at": warn_at,
        }

    async def fetchrow(self, query: str, *args):
        if "SUM" in query:
            day, tenant_id = args
            matching = [
                r for r in self.rows.values() if r["day"] == day and r["tenant_id"] == tenant_id
            ]
            if not matching:
                return {"spent_usd": 0, "cap_usd": 0, "warn_at": 0}
            return {
                "spent_usd": sum(r["spent_usd"] for r in matching),
                "cap_usd": sum(r["cap_usd"] for r in matching),
                "warn_at": min(r["warn_at"] for r in matching),
            }
        ravn_id, tenant_id, day = args
        return self.rows.get((tenant_id, ravn_id, day))


@pytest.fixture(params=["file", "postgres"])
def ledger(request, tmp_path):
    if request.param == "file":
        return FileBudgetLedger(tmp_path / "ledger.json")
    return PostgresBudgetLedger(FakeAsyncpgPool())


class TestBudgetLedger:
    async def test_record_spend_accumulates(self, ledger) -> None:
        await ledger.record_spend(
            "ravn-1", tenant_id="tenant-a", day=_DAY, cost_usd=0.5, cap_usd=1.0, warn_at=0.8
        )
        await ledger.record_spend(
            "ravn-1", tenant_id="tenant-a", day=_DAY, cost_usd=0.25, cap_usd=1.0, warn_at=0.8
        )
        totals = await ledger.totals_for("ravn-1", tenant_id="tenant-a", day=_DAY)
        assert totals.spent_usd == pytest.approx(0.75)
        assert totals.cap_usd == pytest.approx(1.0)
        assert totals.warn_at == pytest.approx(0.8)

    async def test_totals_for_unknown_ravn_is_zero(self, ledger) -> None:
        totals = await ledger.totals_for("nobody", tenant_id="tenant-a", day=_DAY)
        assert totals.spent_usd == 0.0
        assert totals.cap_usd == 0.0
        assert totals.warn_at == 0.0

    async def test_totals_for_wrong_tenant_is_zero_not_leaked(self, ledger) -> None:
        await ledger.record_spend(
            "ravn-1", tenant_id="tenant-a", day=_DAY, cost_usd=5.0, cap_usd=10.0, warn_at=0.9
        )
        totals = await ledger.totals_for("ravn-1", tenant_id="tenant-b", day=_DAY)
        assert totals.spent_usd == 0.0

    async def test_same_ravn_id_in_two_tenants_does_not_collide(self, ledger) -> None:
        """Regression: the row key must be (tenant_id, ravn_id, day), not
        just (ravn_id, day) — otherwise two tenants that both happen to use
        ravn_id "ravn-1" overwrite/merge into one shared row."""
        await ledger.record_spend(
            "ravn-1", tenant_id="tenant-a", day=_DAY, cost_usd=1.0, cap_usd=5.0, warn_at=0.8
        )
        await ledger.record_spend(
            "ravn-1", tenant_id="tenant-b", day=_DAY, cost_usd=9.0, cap_usd=20.0, warn_at=0.5
        )

        totals_a = await ledger.totals_for("ravn-1", tenant_id="tenant-a", day=_DAY)
        totals_b = await ledger.totals_for("ravn-1", tenant_id="tenant-b", day=_DAY)
        assert totals_a.spent_usd == pytest.approx(1.0)
        assert totals_b.spent_usd == pytest.approx(9.0)

    async def test_fleet_totals_sums_within_tenant_only(self, ledger) -> None:
        await ledger.record_spend(
            "ravn-1", tenant_id="tenant-a", day=_DAY, cost_usd=1.0, cap_usd=2.0, warn_at=0.8
        )
        await ledger.record_spend(
            "ravn-2", tenant_id="tenant-a", day=_DAY, cost_usd=2.0, cap_usd=3.0, warn_at=0.5
        )
        await ledger.record_spend(
            "ravn-3", tenant_id="tenant-b", day=_DAY, cost_usd=100.0, cap_usd=100.0, warn_at=0.9
        )

        totals = await ledger.fleet_totals(day=_DAY, tenant_id="tenant-a")
        assert totals.spent_usd == pytest.approx(3.0)
        assert totals.cap_usd == pytest.approx(5.0)
        assert totals.warn_at == pytest.approx(0.5)


class TestFileBudgetLedgerDurability:
    async def test_survives_process_restart(self, tmp_path) -> None:
        path = tmp_path / "ledger.json"
        ledger_a = FileBudgetLedger(path)
        await ledger_a.record_spend(
            "ravn-1", tenant_id="tenant-a", day=_DAY, cost_usd=0.42, cap_usd=1.0, warn_at=0.8
        )

        ledger_b = FileBudgetLedger(path)
        totals = await ledger_b.totals_for("ravn-1", tenant_id="tenant-a", day=_DAY)
        assert totals.spent_usd == pytest.approx(0.42)

    async def test_corrupt_json_raises(self, tmp_path) -> None:
        path = tmp_path / "ledger.json"
        path.write_text("not json", encoding="utf-8")
        ledger = FileBudgetLedger(path)
        with pytest.raises(ValueError, match="not readable/valid JSON"):
            await ledger.totals_for("ravn-1", tenant_id="tenant-a", day=_DAY)

    async def test_no_leftover_tmp_file(self, tmp_path) -> None:
        path = tmp_path / "ledger.json"
        ledger = FileBudgetLedger(path)
        await ledger.record_spend(
            "ravn-1", tenant_id="tenant-a", day=_DAY, cost_usd=1.0, cap_usd=1.0, warn_at=0.8
        )
        assert list(tmp_path.glob("*.tmp")) == []
