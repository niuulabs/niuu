"""Tests for DailyBudgetTracker and DriveLoop budget integration (NIU-570)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.config import BudgetConfig, InitiativeConfig, Settings
from ravn.domain.budget import DailyBudgetTracker
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import AgentTask, OutputMode, TokenUsage
from ravn.drive_loop import DriveLoop


def _make_drive_loop_with_mock_settings(
    tmp_path: Path,
    budget: DailyBudgetTracker | None = None,
) -> tuple[DriveLoop, MagicMock, list[RavnEvent]]:
    """Build DriveLoop with MagicMock settings (no BudgetConfig) to test fallback paths."""
    journal = tmp_path / "queue.json"
    config = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=2,
        task_queue_max=10,
        queue_journal_path=str(journal),
    )
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = False
    published: list[RavnEvent] = []

    mock_publisher = AsyncMock()
    mock_publisher.publish = AsyncMock(side_effect=lambda e: published.append(e))

    loop = DriveLoop(
        agent_factory=MagicMock(return_value=MagicMock()),
        config=config,
        settings=settings,
        event_publisher=mock_publisher,
        budget=budget,
    )
    return loop, MagicMock(), published


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tracker(cap: float = 1.0, warn_at: int = 80) -> DailyBudgetTracker:
    return DailyBudgetTracker(daily_cap_usd=cap, warn_at_percent=warn_at)


def _make_task(title: str = "test task") -> AgentTask:
    hex_ts = hex(int(time.time() * 1000))[2:]
    return AgentTask(
        task_id=f"task_{hex_ts}_0001",
        title=title,
        initiative_context="do something",
        triggered_by="cron:test",
        output_mode=OutputMode.SILENT,
    )


def _make_drive_loop(
    tmp_path: Path,
    budget: DailyBudgetTracker | None = None,
) -> tuple[DriveLoop, MagicMock, list[RavnEvent]]:
    journal = tmp_path / "queue.json"
    config = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=2,
        task_queue_max=10,
        queue_journal_path=str(journal),
    )
    settings = Settings()
    factory = MagicMock(return_value=MagicMock())
    published: list[RavnEvent] = []

    mock_publisher = AsyncMock()
    mock_publisher.publish = AsyncMock(side_effect=lambda e: published.append(e))

    loop = DriveLoop(
        agent_factory=factory,
        config=config,
        settings=settings,
        event_publisher=mock_publisher,
        budget=budget,
    )
    return loop, factory, published


# ---------------------------------------------------------------------------
# DailyBudgetTracker — basic accounting
# ---------------------------------------------------------------------------


def test_tracker_starts_empty() -> None:
    tracker = _make_tracker()
    assert tracker.spent_today_usd == 0.0
    assert tracker.remaining_usd == 1.0


def test_tracker_can_spend_below_cap() -> None:
    tracker = _make_tracker(cap=1.0)
    assert tracker.can_spend() is True
    assert tracker.can_spend(estimated_cost_usd=0.5) is True


def test_tracker_record_updates_spent() -> None:
    tracker = _make_tracker(cap=1.0)
    tracker.record(0.30)
    assert abs(tracker.spent_today_usd - 0.30) < 1e-9
    assert abs(tracker.remaining_usd - 0.70) < 1e-9


def test_tracker_can_spend_at_exact_cap() -> None:
    tracker = _make_tracker(cap=1.0)
    tracker.record(1.0)
    # Spent == cap → still at boundary → False (can't spend more)
    assert tracker.can_spend() is False


def test_tracker_can_spend_over_cap() -> None:
    tracker = _make_tracker(cap=0.50)
    tracker.record(0.60)
    assert tracker.can_spend() is False
    assert tracker.remaining_usd == 0.0


def test_tracker_remaining_never_negative() -> None:
    tracker = _make_tracker(cap=0.10)
    tracker.record(0.50)
    assert tracker.remaining_usd == 0.0


def test_tracker_zero_cap_always_blocks() -> None:
    tracker = _make_tracker(cap=0.0)
    assert tracker.can_spend() is False
    assert tracker.can_spend(estimated_cost_usd=0.0) is False


# ---------------------------------------------------------------------------
# DailyBudgetTracker — warn threshold
# ---------------------------------------------------------------------------


def test_tracker_warn_threshold_not_reached_initially() -> None:
    tracker = _make_tracker(cap=1.0, warn_at=80)
    assert tracker.warn_threshold_reached is False


def test_tracker_warn_threshold_crossed() -> None:
    tracker = _make_tracker(cap=1.0, warn_at=80)
    tracker.record(0.80)
    assert tracker.warn_threshold_reached is True


def test_tracker_warn_threshold_below() -> None:
    tracker = _make_tracker(cap=1.0, warn_at=80)
    tracker.record(0.79)
    assert tracker.warn_threshold_reached is False


def test_tracker_warn_threshold_zero_cap() -> None:
    tracker = _make_tracker(cap=0.0, warn_at=80)
    assert tracker.warn_threshold_reached is False


# ---------------------------------------------------------------------------
# DailyBudgetTracker — UTC day rollover
# ---------------------------------------------------------------------------


def test_tracker_resets_on_day_rollover() -> None:
    tracker = _make_tracker(cap=1.0)
    tracker.record(0.90)
    assert abs(tracker.spent_today_usd - 0.90) < 1e-9

    # Simulate day rollover by moving _current_date to yesterday
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    tracker._current_date = yesterday

    # Accessing any property triggers reset
    assert tracker.spent_today_usd == 0.0
    assert tracker.remaining_usd == 1.0
    assert tracker.can_spend() is True


def test_tracker_record_after_day_rollover_starts_fresh() -> None:
    tracker = _make_tracker(cap=1.0)
    tracker.record(0.99)

    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    tracker._current_date = yesterday

    tracker.record(0.10)
    assert abs(tracker.spent_today_usd - 0.10) < 1e-9


# ---------------------------------------------------------------------------
# BudgetConfig defaults
# ---------------------------------------------------------------------------


def test_budget_config_defaults() -> None:
    cfg = BudgetConfig()
    assert cfg.daily_cap_usd == 1.0
    assert cfg.input_token_cost_per_million == 3.0
    assert cfg.output_token_cost_per_million == 15.0
    assert cfg.warn_at_percent == 80


def test_settings_has_budget_field() -> None:
    settings = Settings()
    assert hasattr(settings, "budget")
    assert isinstance(settings.budget, BudgetConfig)


# ---------------------------------------------------------------------------
# DriveLoop — budget pre-check: task skipped when over cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_skips_task_when_budget_exceeded(tmp_path: Path) -> None:
    """When the budget cap is reached, run_turn must NOT be called."""
    exhausted_tracker = _make_tracker(cap=0.0)  # cap=0 → always blocked

    loop, factory, published = _make_drive_loop(tmp_path, budget=exhausted_tracker)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)
    factory.return_value = mock_agent

    task = _make_task()
    await loop._run_task(task)

    mock_agent.run_turn.assert_not_called()


@pytest.mark.asyncio
async def test_drive_loop_does_not_reenqueue_when_budget_exceeded(
    tmp_path: Path,
) -> None:
    """Skipped budget tasks must NOT be re-enqueued (they survive in the journal)."""
    exhausted_tracker = _make_tracker(cap=0.0)
    loop, factory, _ = _make_drive_loop(tmp_path, budget=exhausted_tracker)

    mock_agent = AsyncMock()
    factory.return_value = mock_agent

    task = _make_task()
    assert loop._queue.empty()

    await loop._run_task(task)

    # Queue must remain empty — no infinite re-enqueue loop
    assert loop._queue.empty()


# ---------------------------------------------------------------------------
# DriveLoop — budget post-execution: cost recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_records_cost_after_task(tmp_path: Path) -> None:
    """After a successful run_turn, budget.record() must be called with the cost."""
    tracker = _make_tracker(cap=10.0)
    loop, factory, _ = _make_drive_loop(tmp_path, budget=tracker)

    # Agent returns a TurnResult-like object with token usage
    fake_usage = TokenUsage(input_tokens=100_000, output_tokens=10_000)

    class FakeTurnResult:
        usage = fake_usage

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=FakeTurnResult())
    factory.return_value = mock_agent

    task = _make_task()

    with patch.object(loop._settings.cascade, "enabled", False):
        await loop._run_task(task)

    # Expected cost: (100_000 * 3.0 + 10_000 * 15.0) / 1_000_000 = 0.45
    expected = (100_000 * 3.0 + 10_000 * 15.0) / 1_000_000
    assert abs(tracker.spent_today_usd - expected) < 1e-9


@pytest.mark.asyncio
async def test_drive_loop_records_zero_cost_when_no_usage(tmp_path: Path) -> None:
    """If run_turn returns None (no usage), budget is unchanged."""
    tracker = _make_tracker(cap=10.0)
    loop, factory, _ = _make_drive_loop(tmp_path, budget=tracker)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)
    factory.return_value = mock_agent

    task = _make_task()

    with patch.object(loop._settings.cascade, "enabled", False):
        await loop._run_task(task)

    assert tracker.spent_today_usd == 0.0


# ---------------------------------------------------------------------------
# DriveLoop — budget warning event published at threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_publishes_warning_at_threshold(tmp_path: Path) -> None:
    """A DECISION event with urgency=0.5 is published when warn_threshold_reached."""
    # Tracker already at 80% → next record pushes it over the threshold
    tracker = _make_tracker(cap=1.0, warn_at=80)
    tracker.record(0.80)  # exactly at warn threshold
    assert tracker.warn_threshold_reached is True

    loop, factory, published = _make_drive_loop(tmp_path, budget=tracker)

    class FakeTurnResult:
        usage = TokenUsage(input_tokens=0, output_tokens=0)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=FakeTurnResult())
    factory.return_value = mock_agent

    task = _make_task()

    with patch.object(loop._settings.cascade, "enabled", False):
        await loop._run_task(task)

    warning_events = [
        e for e in published if e.type == RavnEventType.DECISION and e.payload.get("budget_warning")
    ]
    assert len(warning_events) == 1
    assert warning_events[0].urgency == 0.5


@pytest.mark.asyncio
async def test_drive_loop_no_warning_below_threshold(tmp_path: Path) -> None:
    """No DECISION warning event when spend is below the warn_at_percent threshold."""
    tracker = _make_tracker(cap=1.0, warn_at=80)
    tracker.record(0.10)  # well below 80%

    loop, factory, published = _make_drive_loop(tmp_path, budget=tracker)

    class FakeTurnResult:
        usage = TokenUsage(input_tokens=0, output_tokens=0)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=FakeTurnResult())
    factory.return_value = mock_agent

    task = _make_task()

    with patch.object(loop._settings.cascade, "enabled", False):
        await loop._run_task(task)

    warning_events = [
        e for e in published if e.type == RavnEventType.DECISION and e.payload.get("budget_warning")
    ]
    assert len(warning_events) == 0


@pytest.mark.asyncio
async def test_drive_loop_warning_emitted_only_once_per_day(tmp_path: Path) -> None:
    """Budget warning event is published at most once per UTC day."""
    tracker = _make_tracker(cap=1.0, warn_at=80)
    tracker.record(0.80)  # at threshold

    loop, factory, published = _make_drive_loop(tmp_path, budget=tracker)

    class FakeTurnResult:
        usage = TokenUsage(input_tokens=0, output_tokens=0)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=FakeTurnResult())
    factory.return_value = mock_agent

    # Run two tasks in a row — warning should fire only once
    task1 = _make_task("task-one")
    task2 = _make_task("task-two")

    with patch.object(loop._settings.cascade, "enabled", False):
        await loop._run_task(task1)
        await loop._run_task(task2)

    warning_events = [
        e for e in published if e.type == RavnEventType.DECISION and e.payload.get("budget_warning")
    ]
    assert len(warning_events) == 1


def test_tracker_warn_emitted_resets_on_day_rollover() -> None:
    """_warn_emitted_today resets when the UTC day rolls over."""
    tracker = _make_tracker(cap=1.0, warn_at=80)
    tracker.record(0.80)
    tracker.mark_warn_emitted()
    assert tracker.warn_emitted_today is True

    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    tracker._current_date = yesterday

    # Accessing any property triggers reset
    assert tracker.warn_emitted_today is False


# ---------------------------------------------------------------------------
# DriveLoop — heartbeat includes budget fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_heartbeat_includes_budget(tmp_path: Path) -> None:
    """Heartbeat payload must contain budget_spent_usd and budget_remaining_usd."""
    tracker = _make_tracker(cap=1.0)
    tracker.record(0.25)

    loop, _, published = _make_drive_loop(tmp_path, budget=tracker)

    # Trigger a single heartbeat tick by running _heartbeat with a very short interval
    loop._config = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=2,
        task_queue_max=10,
        queue_journal_path=str(tmp_path / "q.json"),
        heartbeat_interval_seconds=0,  # fire immediately
    )

    # Run heartbeat for one iteration then cancel
    try:
        await asyncio.wait_for(loop._heartbeat(), timeout=0.2)
    except TimeoutError:
        pass

    heartbeat_events = [e for e in published if e.payload.get("heartbeat")]
    assert len(heartbeat_events) >= 1
    hb = heartbeat_events[0]
    assert "budget_spent_usd" in hb.payload
    assert "budget_remaining_usd" in hb.payload
    assert abs(hb.payload["budget_spent_usd"] - 0.25) < 1e-6
    assert abs(hb.payload["budget_remaining_usd"] - 0.75) < 1e-6


# ---------------------------------------------------------------------------
# DriveLoop — fallback rates when settings is not BudgetConfig
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_uses_fallback_rates_with_mock_settings(tmp_path: Path) -> None:
    """When settings.budget is not a BudgetConfig, default rates (3.0/15.0) are used."""
    tracker = _make_tracker(cap=10.0)
    loop, _, _ = _make_drive_loop_with_mock_settings(tmp_path, budget=tracker)

    class FakeTurnResult:
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=FakeTurnResult())
    loop._agent_factory = MagicMock(return_value=mock_agent)

    task = _make_task()
    await loop._run_task(task)

    # With fallback rates: (1M * 3.0 + 1M * 15.0) / 1M = 18.0
    expected = (1_000_000 * 3.0 + 1_000_000 * 15.0) / 1_000_000
    assert abs(tracker.spent_today_usd - expected) < 1e-9


@pytest.mark.asyncio
async def test_drive_loop_with_mock_settings_budget_uses_defaults(tmp_path: Path) -> None:
    """DriveLoop created with MagicMock settings uses default 1.0 USD cap."""
    loop, _, _ = _make_drive_loop_with_mock_settings(tmp_path)
    # Default cap of 1.0 USD means can_spend() starts True
    assert loop._budget.can_spend() is True
    assert abs(loop._budget.remaining_usd - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# DriveLoop — BudgetConfig creation path from real Settings
# ---------------------------------------------------------------------------


def test_drive_loop_budget_initialized_from_settings(tmp_path: Path) -> None:
    """DriveLoop creates DailyBudgetTracker from real Settings.budget config."""
    journal = tmp_path / "queue.json"
    config = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=2,
        task_queue_max=10,
        queue_journal_path=str(journal),
    )
    settings = Settings()
    settings.budget.daily_cap_usd = 2.5
    settings.budget.warn_at_percent = 70

    loop = DriveLoop(agent_factory=MagicMock(), config=config, settings=settings)

    assert abs(loop._budget.remaining_usd - 2.5) < 1e-9


# ---------------------------------------------------------------------------
# DriveLoop — _save_task_output coverage
# ---------------------------------------------------------------------------


def test_save_task_output_writes_file(tmp_path: Path) -> None:
    """_save_task_output writes header + response text when output_path is set."""
    loop, _, _ = _make_drive_loop(tmp_path)
    out_file = tmp_path / "output" / "result.md"

    from ravn.domain.models import AgentTask, OutputMode

    task = AgentTask(
        task_id="task_output_001",
        title="My Task",
        initiative_context="do something",
        triggered_by="cron:test",
        output_mode=OutputMode.SILENT,
        output_path=out_file,
    )

    channel = MagicMock()
    channel.response_text = "agent response here"

    loop._save_task_output(task, channel)

    assert out_file.exists()
    content = out_file.read_text()
    assert "My Task" in content
    assert "agent response here" in content
    assert "cron:test" in content


def test_save_task_output_skips_when_no_path(tmp_path: Path) -> None:
    """_save_task_output returns early when output_path is None."""
    loop, _, _ = _make_drive_loop(tmp_path)

    from ravn.domain.models import AgentTask, OutputMode

    task = AgentTask(
        task_id="task_nopath_001",
        title="No Path Task",
        initiative_context="do something",
        triggered_by="cron:test",
        output_mode=OutputMode.SILENT,
        output_path=None,
    )

    channel = MagicMock()
    channel.response_text = "ignored"

    # Should not raise
    loop._save_task_output(task, channel)


def test_save_task_output_handles_write_error(tmp_path: Path) -> None:
    """_save_task_output logs a warning but does not raise on write failure."""
    loop, _, _ = _make_drive_loop(tmp_path)

    from unittest.mock import patch

    from ravn.domain.models import AgentTask, OutputMode

    task = AgentTask(
        task_id="task_err_001",
        title="Error Task",
        initiative_context="do something",
        triggered_by="cron:test",
        output_mode=OutputMode.SILENT,
        output_path=tmp_path / "out.md",
    )

    channel = MagicMock()
    channel.response_text = "text"

    with patch("ravn.drive_loop.logger") as mock_logger:
        with patch.object(type(task.output_path), "write_text", side_effect=OSError("disk full")):
            loop._save_task_output(task, channel)

    mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# DriveLoop — _trigger_watcher error handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_watcher_handles_unexpected_exception(tmp_path: Path) -> None:
    """_trigger_watcher catches non-CancelledError exceptions without propagating."""
    loop, _, _ = _make_drive_loop(tmp_path)

    from ravn.ports.trigger import TriggerPort

    class BrokenTrigger(TriggerPort):
        @property
        def name(self) -> str:
            return "broken"

        async def run(self, enqueue):  # type: ignore[override]
            raise RuntimeError("trigger exploded")

    trigger = BrokenTrigger()
    # Should complete without raising
    await loop._trigger_watcher(trigger)


# ---------------------------------------------------------------------------
# DriveLoop — journal resume path and OSError on unlink
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_resume_loads_journal(tmp_path: Path) -> None:
    """When resume=True, DriveLoop calls _load_journal on run() start."""
    journal = tmp_path / "queue.json"
    # Journal with no records (empty list)
    journal.write_text("[]")

    config = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=2,
        task_queue_max=10,
        queue_journal_path=str(journal),
    )
    from ravn.config import Settings

    loop = DriveLoop(
        agent_factory=MagicMock(return_value=MagicMock()),
        config=config,
        settings=Settings(),
        resume=True,
    )

    # _load_journal is called inside run() but we can call it directly to test
    loop._load_journal()
    # Should not raise and queue should be empty (no records in journal)
    assert loop._queue.empty()


def test_load_journal_returns_early_when_file_missing(tmp_path: Path) -> None:
    """_load_journal returns early (no error) when journal file does not exist."""
    config = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=2,
        task_queue_max=10,
        queue_journal_path=str(tmp_path / "nonexistent.json"),
    )
    from ravn.config import Settings

    loop = DriveLoop(
        agent_factory=MagicMock(return_value=MagicMock()),
        config=config,
        settings=Settings(),
        resume=True,
    )

    # Journal file does not exist — should return early without error
    loop._load_journal()
    assert loop._queue.empty()
