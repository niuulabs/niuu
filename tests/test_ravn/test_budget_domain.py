"""Tests for ravn.domain.budget: DailyBudgetTracker.seed and resolve_task_cost_usd."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bifrost.config import PricingOverride
from ravn.domain.budget import DailyBudgetTracker, UnpricedModelError, resolve_task_cost_usd


def _override(
    *,
    input_per_million: float = 0.0,
    output_per_million: float = 0.0,
) -> PricingOverride:
    """The real type ravn.config.BudgetConfig.pricing_overrides holds."""
    return PricingOverride(
        input_per_million=input_per_million, output_per_million=output_per_million
    )


class TestDailyBudgetTrackerSeed:
    def test_seed_hydrates_spent_today_from_durable_state(self) -> None:
        tracker = DailyBudgetTracker(daily_cap_usd=1.0, warn_at_percent=80)
        today = datetime.now(UTC).date()
        tracker.seed(0.65, day=today)
        assert tracker.spent_today_usd == pytest.approx(0.65)
        assert tracker.remaining_usd == pytest.approx(0.35)

    def test_seed_with_spend_already_over_warn_threshold_marks_it_reached(self) -> None:
        tracker = DailyBudgetTracker(daily_cap_usd=1.0, warn_at_percent=80)
        today = datetime.now(UTC).date()
        tracker.seed(0.9, day=today)
        assert tracker.warn_threshold_reached is True

    def test_seed_for_a_past_day_is_superseded_by_the_next_reset_check(self) -> None:
        tracker = DailyBudgetTracker(daily_cap_usd=1.0, warn_at_percent=80)
        yesterday = datetime.now(UTC).date() - timedelta(days=1)
        tracker.seed(0.9, day=yesterday)
        # Any read re-checks the UTC day and resets a stale seed.
        assert tracker.spent_today_usd == 0.0


class TestResolveTaskCostUsd:
    def test_bifrost_source_uses_the_catalog_rate_for_a_known_model(self) -> None:
        cost = resolve_task_cost_usd(
            "claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=0,
            pricing_source="bifrost",
            flat_input_per_million=999.0,
            flat_output_per_million=999.0,
        )
        # Catalog rate for claude-sonnet-4-6 input, not the absurd flat rate.
        assert cost < 999.0
        assert cost > 0

    def test_bifrost_source_raises_for_a_model_the_catalog_does_not_price(self) -> None:
        with pytest.raises(UnpricedModelError, match="add it to budget.pricing_overrides"):
            resolve_task_cost_usd(
                "some-local-model-bifrost-has-never-heard-of",
                input_tokens=1_000_000,
                output_tokens=0,
                pricing_source="bifrost",
                flat_input_per_million=7.0,
                flat_output_per_million=20.0,
            )

    def test_bifrost_source_uses_a_pricing_override_for_an_uncataloged_model(self) -> None:
        """The fix for MUST-FIX #5: Nemotron/Qwen/claude-sonnet-4-5 and any
        other model outside BUILTIN_PRICING must be priceable via an
        operator-configured override, not force pricing_source: flat."""
        cost = resolve_task_cost_usd(
            "nemotron-nano-9b-v2",
            input_tokens=1_000_000,
            output_tokens=0,
            pricing_source="bifrost",
            flat_input_per_million=999.0,
            flat_output_per_million=999.0,
            pricing_overrides={
                "nemotron-nano-9b-v2": _override(input_per_million=1.5, output_per_million=6.0)
            },
        )
        assert cost == pytest.approx(1.5)

    def test_pricing_override_takes_precedence_over_the_builtin_catalog(self) -> None:
        cost = resolve_task_cost_usd(
            "claude-sonnet-4-6",  # BUILTIN_PRICING has this at $3.00/million input
            input_tokens=1_000_000,
            output_tokens=0,
            pricing_source="bifrost",
            flat_input_per_million=999.0,
            flat_output_per_million=999.0,
            pricing_overrides={
                "claude-sonnet-4-6": _override(input_per_million=0.5, output_per_million=1.0)
            },
        )
        assert cost == pytest.approx(0.5)

    def test_a_model_still_unpriced_after_overrides_still_raises(self) -> None:
        with pytest.raises(UnpricedModelError, match="add it to budget.pricing_overrides"):
            resolve_task_cost_usd(
                "some-other-model",
                input_tokens=1,
                output_tokens=1,
                pricing_source="bifrost",
                flat_input_per_million=1.0,
                flat_output_per_million=1.0,
                pricing_overrides={"a-different-model": _override(input_per_million=1.0)},
            )

    def test_flat_source_always_uses_the_configured_rate(self) -> None:
        cost = resolve_task_cost_usd(
            "claude-sonnet-4-6",  # even a catalog-known model
            input_tokens=1_000_000,
            output_tokens=0,
            pricing_source="flat",
            flat_input_per_million=7.0,
            flat_output_per_million=20.0,
        )
        assert cost == pytest.approx(7.0)

    def test_flat_source_zero_tokens_is_a_real_zero(self) -> None:
        cost = resolve_task_cost_usd(
            "anything",
            input_tokens=0,
            output_tokens=0,
            pricing_source="flat",
            flat_input_per_million=7.0,
            flat_output_per_million=20.0,
        )
        assert cost == 0.0

    def test_unknown_pricing_source_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown budget.pricing_source"):
            resolve_task_cost_usd(
                "claude-sonnet-4-6",
                input_tokens=1,
                output_tokens=1,
                pricing_source="made-up",
                flat_input_per_million=1.0,
                flat_output_per_million=1.0,
            )
