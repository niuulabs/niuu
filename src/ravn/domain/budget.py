"""Daily budget tracker for per-Ravn API cost accounting (NIU-570).

Tracks USD spend per UTC calendar day and gates new initiative tasks when the
configured daily cap is reached.  Resets automatically at the UTC day boundary.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    input_per_million: float,
    output_per_million: float,
) -> float:
    """Estimate USD cost from token counts using per-million-token rates."""
    return (
        input_tokens * input_per_million / 1_000_000
        + output_tokens * output_per_million / 1_000_000
    )


class UnpricedModelError(ValueError):
    """Raised when pricing_source='bifrost' and the model has no catalog entry."""


def resolve_task_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    pricing_source: str,
    flat_input_per_million: float,
    flat_output_per_million: float,
    pricing_overrides: dict[str, Any] | None = None,
) -> float:
    """Cost for one turn, priced from the explicitly configured source.

    ``pricing_source == "bifrost"`` prices from *pricing_overrides* first
    (``ravn.config.BudgetConfig.pricing_overrides`` — the remedy for a model
    Bifröst's built-in catalog does not carry), then Bifröst's built-in
    model-pricing catalog, and raises :class:`UnpricedModelError` when
    *model* has no entry in either — billing an unpriced (e.g. local) model
    at a generic flat rate would misreport spend, which is worse than a
    loud, fixable error. ``pricing_source == "flat"`` always uses the
    configured flat rate, regardless of model: an explicit operator choice
    for a deployment whose models Bifröst does not price, not an automatic
    fallback.
    """
    if pricing_source == "flat":
        return compute_cost(
            input_tokens,
            output_tokens,
            flat_input_per_million,
            flat_output_per_million,
        )
    if pricing_source != "bifrost":
        raise ValueError(
            f"Unknown budget.pricing_source {pricing_source!r} — expected 'bifrost' or 'flat'"
        )

    from bifrost.domain.models import TokenUsage  # noqa: PLC0415
    from bifrost.pricing import BUILTIN_PRICING, ModelPricing, calculate_cost  # noqa: PLC0415

    overrides: dict[str, ModelPricing] = {}
    for override_model, override in (pricing_overrides or {}).items():
        overrides[override_model] = ModelPricing(
            input_per_million=override.input_per_million,
            output_per_million=override.output_per_million,
            cache_creation_per_million=override.cache_creation_per_million,
            cache_read_per_million=override.cache_read_per_million,
        )

    if model not in overrides and model not in BUILTIN_PRICING:
        raise UnpricedModelError(
            f"budget.pricing_source is 'bifrost' but neither budget.pricing_overrides "
            f"nor Bifröst's built-in catalog has a pricing entry for model {model!r} — "
            "add it to budget.pricing_overrides, or set budget.pricing_source: flat "
            "to bill this deployment's models at a configured flat rate instead"
        )
    usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    return calculate_cost(model, usage, overrides=overrides)


class DailyBudgetTracker:
    """Tracks USD spend per UTC day and gates tasks when the cap is reached.

    Usage::

        tracker = DailyBudgetTracker(daily_cap_usd=1.0, warn_at_percent=80)

        if not tracker.can_spend():
            # skip task — budget exhausted for today
            ...

        tracker.record(outcome.cost_usd)

        if tracker.warn_threshold_reached:
            # publish warning event
            ...
    """

    def __init__(self, daily_cap_usd: float = 1.0, warn_at_percent: int = 80) -> None:
        self._daily_cap_usd = daily_cap_usd
        self._warn_at_percent = warn_at_percent
        self._spent_today: float = 0.0
        self._current_date: date = datetime.now(UTC).date()
        self._warn_emitted_today: bool = False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _maybe_reset(self) -> None:
        """Reset spend counter if the UTC calendar day has rolled over."""
        today = datetime.now(UTC).date()
        if today != self._current_date:
            self._spent_today = 0.0
            self._current_date = today
            self._warn_emitted_today = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, cost_usd: float) -> None:
        """Add spend from a completed task."""
        self._maybe_reset()
        self._spent_today += cost_usd

    def seed(self, spent_usd: float, *, day: date) -> None:
        """Hydrate today's counter from durable state read at startup.

        Called once, before the first turn, with the ledger's own total for
        *day* so a restart resumes the same day's count instead of silently
        forgetting spend already recorded elsewhere (the budget cap would
        otherwise reset to zero on every restart no matter how much was
        already spent today).
        """
        self._current_date = day
        self._spent_today = spent_usd
        self._warn_emitted_today = self.warn_threshold_reached

    def can_spend(self, estimated_cost_usd: float = 0.0) -> bool:
        """Return True if adding *estimated_cost_usd* keeps us within the cap.

        When *estimated_cost_usd* is 0.0 (default) simply checks whether the
        cap has already been reached.
        """
        self._maybe_reset()
        if self._daily_cap_usd <= 0:
            return False
        return self._spent_today + estimated_cost_usd < self._daily_cap_usd

    @property
    def spent_today_usd(self) -> float:
        """USD spent so far today (resets at UTC midnight)."""
        self._maybe_reset()
        return self._spent_today

    @property
    def remaining_usd(self) -> float:
        """USD remaining before the daily cap is hit (never negative)."""
        self._maybe_reset()
        return max(0.0, self._daily_cap_usd - self._spent_today)

    @property
    def warn_threshold_reached(self) -> bool:
        """True when spend has crossed the *warn_at_percent* threshold."""
        self._maybe_reset()
        if self._daily_cap_usd <= 0:
            return False
        pct = (self._spent_today / self._daily_cap_usd) * 100
        return pct >= self._warn_at_percent

    @property
    def warn_emitted_today(self) -> bool:
        """True if the budget warning event has already been emitted today."""
        self._maybe_reset()
        return self._warn_emitted_today

    def mark_warn_emitted(self) -> None:
        """Record that the budget warning event was emitted for today."""
        self._warn_emitted_today = True
