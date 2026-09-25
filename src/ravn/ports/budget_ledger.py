"""Budget ledger port — durable per-ravn, per-UTC-day spend accounting.

Backs both read sides (``GET /api/v1/ravn/budget/{ravnId}`` and
``/budget/fleet``) and the write side: each resident's ``DailyBudgetTracker``
(``ravn.domain.budget``) records real spend here synchronously as it happens,
so the API's answer and the resident's own gating decision come from the same
durable state instead of an in-memory counter that resets on restart.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from ravn.domain.budget_totals import BudgetTotals


class BudgetLedgerPort(ABC):
    """Durable per-(ravn_id, day) spend ledger."""

    @abstractmethod
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
        """Add *cost_usd* to *ravn_id*'s total for *day* (creates the row if absent).

        ``cap_usd``/``warn_at`` are written on every call so the ledger always
        reflects the caller's current configuration, not just the first turn
        of the day's. ``tenant_id`` scopes the row for ``fleet_totals``.
        """
        raise NotImplementedError

    @abstractmethod
    async def totals_for(self, ravn_id: str, *, tenant_id: str, day: date) -> BudgetTotals:
        """Return *ravn_id*'s totals for *day* within *tenant_id*.

        Zero totals when never recorded, or when *ravn_id* belongs to a
        different tenant — the caller must not learn another tenant's spend.
        """
        raise NotImplementedError

    @abstractmethod
    async def fleet_totals(self, *, day: date, tenant_id: str) -> BudgetTotals:
        """Return the sum of every ravn's totals for *day* within *tenant_id*."""
        raise NotImplementedError
