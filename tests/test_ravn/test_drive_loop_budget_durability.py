"""DriveLoop <-> ResidentBudgetPort integration: synchronous recording, restart durability."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ravn.config import InitiativeConfig
from ravn.domain.budget_totals import BudgetTotals
from ravn.drive_loop import DriveLoop

_NO_JOURNAL_PATH = "/dev/null/does-not-exist"


class FakeResidentBudget:
    """In-memory ResidentBudgetPort double — the "durable state" two DriveLoop
    instances share to simulate a restart."""

    def __init__(self) -> None:
        self.spent_usd = 0.0
        self.record_calls: list[dict] = []

    async def seed_today(self) -> float:
        return self.spent_usd

    async def record_spend(self, *, cost_usd, model, cap_usd, warn_at) -> BudgetTotals:
        self.record_calls.append({"cost_usd": cost_usd, "model": model})
        self.spent_usd += cost_usd
        return BudgetTotals(spent_usd=self.spent_usd, cap_usd=cap_usd, warn_at=warn_at)


class RaisingResidentBudget(FakeResidentBudget):
    async def record_spend(self, **kwargs):
        raise ConnectionError("platform unreachable")


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = False
    settings.budget.daily_cap_usd = 1.0
    settings.budget.warn_at_percent = 80
    settings.budget.pricing_source = "flat"
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    settings.llm.model = "a-model-bifrost-does-not-price"
    return settings


def _drive_loop(*, resident_budget) -> DriveLoop:
    cfg = InitiativeConfig(enabled=True, queue_journal_path=_NO_JOURNAL_PATH)
    return DriveLoop(
        agent_factory=MagicMock(),
        config=cfg,
        settings=_settings(),
        resident_budget=resident_budget,
    )


def _turn_result(input_tokens: int, output_tokens: int):
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    result = MagicMock()
    result.usage = usage
    return result


class TestSynchronousRecording:
    async def test_record_task_cost_writes_through_to_resident_budget(self) -> None:
        resident_budget = FakeResidentBudget()
        dl = _drive_loop(resident_budget=resident_budget)
        task = MagicMock(task_id="t1")

        cost = await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=None)

        assert cost == pytest.approx(3.0)  # flat input rate
        assert len(resident_budget.record_calls) == 1
        assert dl._budget.spent_today_usd == pytest.approx(3.0)

    async def test_no_resident_budget_configured_means_no_reports(self) -> None:
        dl = _drive_loop(resident_budget=None)
        task = MagicMock(task_id="t1")

        await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=None)

        # Local gating still works even without durable reporting.
        assert dl._budget.spent_today_usd == pytest.approx(3.0)

    async def test_resident_budget_failure_raises_not_silently_dropped(self) -> None:
        dl = _drive_loop(resident_budget=RaisingResidentBudget())
        task = MagicMock(task_id="t1")

        with pytest.raises(ConnectionError, match="platform unreachable"):
            await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=None)

    async def test_agent_model_override_prices_the_model_that_served_the_turn(self) -> None:
        resident_budget = FakeResidentBudget()
        dl = _drive_loop(resident_budget=resident_budget)
        task = MagicMock(task_id="t1")
        agent = MagicMock()
        agent._model = "claude-sonnet-4-6"  # in Bifröst's catalog

        await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=agent)

        assert resident_budget.record_calls[0]["model"] == "claude-sonnet-4-6"


def _settings_with(
    *, budget_enabled: bool, pricing_source: str, pricing_overrides: dict | None = None
) -> MagicMock:
    """A resident running a model Bifröst's BUILTIN_PRICING does not carry
    (e.g. Nemotron, Qwen, claude-sonnet-4-5)."""
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = False
    settings.budget.enabled = budget_enabled
    settings.budget.daily_cap_usd = 1.0
    settings.budget.warn_at_percent = 80
    settings.budget.pricing_source = pricing_source
    settings.budget.pricing_overrides = pricing_overrides or {}
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    settings.llm.model = "nemotron-nano-9b-v2"  # not in Bifröst's BUILTIN_PRICING
    return settings


def _drive_loop_with(settings: MagicMock, *, resident_budget) -> DriveLoop:
    cfg = InitiativeConfig(enabled=True, queue_journal_path=_NO_JOURNAL_PATH)
    return DriveLoop(
        agent_factory=MagicMock(), config=cfg, settings=settings, resident_budget=resident_budget
    )


class TestFlatPricingIsAlwaysSafe:
    """MUST-FIX: pricing_source defaults to 'flat' precisely so that any
    resident on a model outside Bifröst's 6-entry BUILTIN_PRICING table
    (Nemotron, Qwen, claude-sonnet-4-5, ...) never raises UnpricedModelError
    — pricing always runs (usage events carry cost_usd unconditionally),
    and budget.enabled (default True) only gates cap enforcement, not
    pricing."""

    async def test_unpriced_model_with_flat_pricing_and_budget_on_does_not_raise(self) -> None:
        settings = _settings_with(budget_enabled=True, pricing_source="flat")
        dl = _drive_loop_with(settings, resident_budget=None)
        task = MagicMock(task_id="t1")

        cost = await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=None)

        assert cost == pytest.approx(3.0)  # flat input rate
        assert dl._budget.spent_today_usd == pytest.approx(3.0)

    async def test_unpriced_model_with_flat_pricing_and_budget_off_still_prices(self) -> None:
        """enabled=False is the explicit opt-out from CAP ENFORCEMENT, not
        from pricing — usage events must keep a real cost_usd either way."""
        settings = _settings_with(budget_enabled=False, pricing_source="flat")
        dl = _drive_loop_with(settings, resident_budget=None)
        task = MagicMock(task_id="t1")

        cost = await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=None)

        assert cost == pytest.approx(3.0)
        assert dl._budget.spent_today_usd == pytest.approx(3.0)


class TestBifrostPricingIsAnExplicitOptIn:
    """pricing_source: "bifrost" is opt-in, catalog-accurate pricing — an
    unpriced model raises regardless of budget.enabled, since the operator
    explicitly asked for catalog pricing and none exists."""

    async def test_unpriced_model_with_bifrost_pricing_raises_with_the_remedy(self) -> None:
        settings = _settings_with(budget_enabled=True, pricing_source="bifrost")
        dl = _drive_loop_with(settings, resident_budget=None)
        task = MagicMock(task_id="t1")

        with pytest.raises(Exception, match="add it to budget.pricing_overrides"):
            await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=None)

    async def test_unpriced_model_with_bifrost_pricing_raises_even_if_budget_off(self) -> None:
        settings = _settings_with(budget_enabled=False, pricing_source="bifrost")
        dl = _drive_loop_with(settings, resident_budget=None)
        task = MagicMock(task_id="t1")

        with pytest.raises(Exception, match="add it to budget.pricing_overrides"):
            await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=None)

    async def test_a_pricing_override_for_the_model_is_applied(self) -> None:
        from bifrost.config import PricingOverride

        settings = _settings_with(
            budget_enabled=True,
            pricing_source="bifrost",
            pricing_overrides={"nemotron-nano-9b-v2": PricingOverride(input_per_million=1.5)},
        )
        dl = _drive_loop_with(settings, resident_budget=None)
        task = MagicMock(task_id="t1")

        cost = await dl._record_task_cost(task, _turn_result(1_000_000, 0), agent=None)

        assert cost == pytest.approx(1.5)


class TestRestartDurability:
    async def test_a_second_drive_loop_resumes_the_first_ones_spend(self) -> None:
        shared_budget = FakeResidentBudget()

        dl_a = _drive_loop(resident_budget=shared_budget)
        await dl_a._record_task_cost(MagicMock(task_id="t1"), _turn_result(1_000_000, 0), None)
        await dl_a._record_task_cost(MagicMock(task_id="t2"), _turn_result(500_000, 0), None)
        assert dl_a._budget.spent_today_usd == pytest.approx(4.5)

        # Simulate a restart: a brand-new DriveLoop, same durable state
        # source. Before any task runs, it must already know today's real
        # spend rather than starting the day over at zero.
        dl_b = _drive_loop(resident_budget=shared_budget)
        assert dl_b._budget.spent_today_usd == 0.0  # not seeded yet
        await dl_b._seed_budget_from_ledger()
        assert dl_b._budget.spent_today_usd == pytest.approx(4.5)
