"""Budget totals — the API-layer shape returned by ``/ravn/budget/*``.

``warn_at`` is a 0-1 fraction of ``cap_usd`` end to end (API payload, ledger
storage, and the web client all agree on this unit) — never a 0-100 percent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetTotals:
    """Spend totals for one ravn (or the whole fleet) for one UTC day."""

    spent_usd: float
    cap_usd: float
    warn_at: float


#: The answer for "no spend recorded yet today" — a real zero, not a degraded
#: placeholder: an unconfigured cap and no spend is the true state before the
#: first turn of the day runs.
EMPTY_BUDGET_TOTALS = BudgetTotals(spent_usd=0.0, cap_usd=0.0, warn_at=0.0)
