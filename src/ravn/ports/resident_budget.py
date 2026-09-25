"""Resident-side budget reporting port.

Distinct from :class:`ravn.ports.budget_ledger.BudgetLedgerPort` (the
API-side persistence port). A resident never carries its own ``ravn_id``/
``tenant_id`` config or Postgres credentials — it reports spend through this
port, and either a platform-API adapter (identity-derived, production) or a
local adapter (explicit mini-mode choice) decides how that becomes durable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.budget_totals import BudgetTotals


class ResidentBudgetPort(ABC):
    """How a resident reads its own starting spend and reports new spend."""

    @abstractmethod
    async def seed_today(self) -> float:
        """Return this resident's own USD spend already recorded today."""
        raise NotImplementedError

    @abstractmethod
    async def record_spend(
        self,
        *,
        cost_usd: float,
        model: str,
        cap_usd: float,
        warn_at: float,
    ) -> BudgetTotals:
        """Report *cost_usd* spent on *model* and return the updated totals.

        ``cap_usd``/``warn_at`` are this resident's own configured budget —
        recorded alongside the spend so the API's read side always reflects
        the caller's current configuration.
        """
        raise NotImplementedError
