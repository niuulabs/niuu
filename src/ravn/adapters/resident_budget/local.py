"""Report budget spend directly to a local ledger — explicit mini-mode adapter.

Mini mode runs the Ravn API and its one resident in the same process/host
with no separate platform boundary to call, so there is nothing to derive an
identity from over HTTP. This adapter is an explicit operator choice (set via
``adapter:`` in config, never the default) and is the only place a resident
process still names its own ``ravn_id``/``tenant_id`` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from niuu.utils import import_class
from ravn.domain.budget_totals import BudgetTotals
from ravn.ports.budget_ledger import BudgetLedgerPort


class LocalBudgetReporter:
    """Wraps a :class:`BudgetLedgerPort` in-process for single-host mini mode.

    Builds its own ledger from ``ledger_adapter``/``ledger_kwargs`` (a nested
    dynamic-adapter reference) rather than taking a live port instance —
    every dynamic adapter's constructor kwargs stay plain values, so this one
    is configurable the same way trigger_store/budget_ledger are.
    """

    def __init__(
        self,
        *,
        ravn_id: str,
        tenant_id: str,
        ledger_adapter: str = "ravn.adapters.budget_ledger.FileBudgetLedger",
        ledger_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if not ravn_id or not tenant_id:
            raise ValueError(
                "LocalBudgetReporter requires both ravn_id and tenant_id — "
                "mini mode names its own resident explicitly since there is "
                "no platform boundary to derive them from"
            )
        self._ledger: BudgetLedgerPort = import_class(ledger_adapter)(**(ledger_kwargs or {}))
        self._ravn_id = ravn_id
        self._tenant_id = tenant_id

    async def seed_today(self) -> float:
        totals = await self._ledger.totals_for(
            self._ravn_id,
            tenant_id=self._tenant_id,
            day=datetime.now(UTC).date(),
        )
        return totals.spent_usd

    async def record_spend(
        self,
        *,
        cost_usd: float,
        model: str,
        cap_usd: float,
        warn_at: float,
    ) -> BudgetTotals:
        del model  # local mode has no separate pricing catalog call to make
        day = datetime.now(UTC).date()
        await self._ledger.record_spend(
            self._ravn_id,
            tenant_id=self._tenant_id,
            day=day,
            cost_usd=cost_usd,
            cap_usd=cap_usd,
            warn_at=warn_at,
        )
        return await self._ledger.totals_for(self._ravn_id, tenant_id=self._tenant_id, day=day)
