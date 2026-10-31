"""Unit tests for DreamCycleTrigger."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ravn.adapters.triggers.cron import CronExpressionError
from ravn.adapters.triggers.dream_cycle import DreamCycleTrigger
from ravn.config import DreamCycleTriggerConfig
from ravn.domain.models import AgentTask, OutputMode

_SCHEDULED = datetime(2026, 4, 12, 3, 0, tzinfo=UTC)


def _make_config(
    enabled: bool = True,
    cron_expression: str = "0 3 * * *",
    persona: str = "mimir-curator",
    task_description: str = "Nightly dream cycle: enrich, lint, cross-reference",
    token_budget_usd: float = 0.50,
    poll_interval_seconds: int = 60,
    autonomy_mode: str = "guarded",
    environment_id: str = "",
    proposal_store_path: str = "~/.ravn/autonomy_proposals.json",
) -> DreamCycleTriggerConfig:
    return DreamCycleTriggerConfig(
        enabled=enabled,
        cron_expression=cron_expression,
        persona=persona,
        task_description=task_description,
        token_budget_usd=token_budget_usd,
        poll_interval_seconds=poll_interval_seconds,
        autonomy_mode=autonomy_mode,
        environment_id=environment_id,
        proposal_store_path=proposal_store_path,
    )


def _make_trigger(
    config: DreamCycleTriggerConfig | None = None,
    state_dir: Path | None = None,
) -> DreamCycleTrigger:
    if config is None:
        config = _make_config()
    # Never the real ~/.ravn/daemon: every poll persists state.
    return DreamCycleTrigger(config=config, state_dir=state_dir or Path(tempfile.mkdtemp()))


def _patch_now(now: datetime):
    """Freeze ``datetime.now`` inside the trigger module at *now*."""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return now

    return patch("ravn.adapters.triggers.dream_cycle.datetime", _FrozenDatetime)


class TestDreamCycleTriggerName:
    def test_name(self) -> None:
        assert _make_trigger().name == "dream_cycle"


class TestDreamCycleTriggerDisabled:
    @pytest.mark.asyncio
    async def test_run_exits_immediately_when_disabled(self) -> None:
        trigger = _make_trigger(config=_make_config(enabled=False))
        enqueue = AsyncMock()

        # Should return without looping or sleeping
        await trigger.run(enqueue)
        enqueue.assert_not_called()


class TestDreamCycleTriggerScheduledRun:
    def test_returns_the_matching_minute_inside_the_window(self) -> None:
        trigger = _make_trigger()
        after = datetime(2026, 4, 12, 2, 50, tzinfo=UTC)
        upto = datetime(2026, 4, 12, 3, 10, tzinfo=UTC)

        assert trigger._scheduled_run(after, upto) == _SCHEDULED

    def test_window_excludes_its_start(self) -> None:
        trigger = _make_trigger()

        assert trigger._scheduled_run(_SCHEDULED, _SCHEDULED + timedelta(minutes=5)) is None

    def test_returns_none_when_no_minute_matches(self) -> None:
        trigger = _make_trigger()
        after = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)

        assert trigger._scheduled_run(after, after + timedelta(hours=2)) is None

    def test_returns_the_latest_of_several_missed_runs(self) -> None:
        trigger = _make_trigger()
        after = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)

        assert trigger._scheduled_run(after, datetime(2026, 4, 12, 9, 0, tzinfo=UTC)) == _SCHEDULED

    def test_invalid_schedule_raises_when_built(self) -> None:
        with pytest.raises(CronExpressionError):
            _make_trigger(config=_make_config(cron_expression="0 3 * * MON-FRI"))

    def test_disabled_trigger_does_not_validate_the_schedule(self) -> None:
        _make_trigger(config=_make_config(enabled=False, cron_expression="not a cron"))


class TestDreamCycleTriggerMissedRun:
    @pytest.mark.asyncio
    async def test_run_missed_during_a_restart_fires_on_the_next_poll(self, tmp_path: Path) -> None:
        """The resident was restarting at 03:00; the first poll at 03:04 runs the dream."""
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._last_checked_at = datetime(2026, 4, 12, 2, 58, tzinfo=UTC)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with _patch_now(datetime(2026, 4, 12, 3, 4, 30, tzinfo=UTC)):
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 1
        assert trigger._last_dream_at == datetime(2026, 4, 12, 3, 4, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_restart_loads_legacy_state_without_rerunning_last_dream(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "dream_cycle_state.json").write_text(
            json.dumps({"last_dream_at": "2026-04-12T03:00:41+00:00"}), encoding="utf-8"
        )
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._load_state()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with _patch_now(datetime(2026, 4, 12, 20, 0, tzinfo=UTC)):
            await trigger._poll_once(_enqueue)

        assert enqueued == []
        saved = json.loads((tmp_path / "dream_cycle_state.json").read_text())
        assert saved["last_checked_at"] == "2026-04-12T20:00:00+00:00"

    @pytest.mark.asyncio
    async def test_first_start_does_not_backfill_history(self, tmp_path: Path) -> None:
        trigger = _make_trigger(state_dir=tmp_path)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with _patch_now(datetime(2026, 4, 12, 9, 0, tzinfo=UTC)):
            await trigger._poll_once(_enqueue)

        assert enqueued == []

    @pytest.mark.asyncio
    async def test_rejected_enqueue_keeps_last_dream_at(self, tmp_path: Path) -> None:
        """A dream already queued covers the run; it is not recorded as a new dream."""
        trigger = _make_trigger(state_dir=tmp_path)
        previous = datetime(2026, 4, 11, 3, 0, tzinfo=UTC)
        trigger._last_dream_at = previous
        trigger._last_checked_at = datetime(2026, 4, 12, 2, 59, tzinfo=UTC)

        async def _enqueue(task: AgentTask) -> bool:
            return False

        with _patch_now(datetime(2026, 4, 12, 3, 0, tzinfo=UTC)):
            await trigger._poll_once(_enqueue)

        assert trigger._last_dream_at == previous
        assert trigger._last_checked_at == datetime(2026, 4, 12, 3, 0, tzinfo=UTC)


class TestDreamCycleTriggerPollOnce:
    @pytest.mark.asyncio
    async def test_poll_once_skips_when_not_due(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=None):
            await trigger._poll_once(_enqueue)

        assert enqueued == []

    @pytest.mark.asyncio
    async def test_poll_once_enqueues_task_when_due(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED):
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 1
        task = enqueued[0]
        assert task.persona == "mimir-curator"
        assert task.triggered_by == "dream_cycle:cron"
        assert task.output_mode == OutputMode.SILENT
        assert "Dream cycle run" in task.initiative_context

    @pytest.mark.asyncio
    async def test_second_poll_in_the_same_minute_does_not_fire_again(self, tmp_path: Path) -> None:
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._last_checked_at = datetime(2026, 4, 12, 2, 59, tzinfo=UTC)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with _patch_now(datetime(2026, 4, 12, 3, 0, 5, tzinfo=UTC)):
            await trigger._poll_once(_enqueue)
        with _patch_now(datetime(2026, 4, 12, 3, 0, 50, tzinfo=UTC)):
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 1

    @pytest.mark.asyncio
    async def test_fires_again_at_the_next_scheduled_minute(self, tmp_path: Path) -> None:
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._last_checked_at = datetime(2026, 4, 12, 2, 59, tzinfo=UTC)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with _patch_now(datetime(2026, 4, 12, 3, 0, tzinfo=UTC)):
            await trigger._poll_once(_enqueue)
        with _patch_now(datetime(2026, 4, 13, 3, 0, tzinfo=UTC)):
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 2


class TestDreamCycleTriggerTaskContent:
    @pytest.mark.asyncio
    async def test_task_uses_configured_persona(self) -> None:
        config = _make_config(persona="custom-persona")
        trigger = _make_trigger(config=config)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED):
            await trigger._poll_once(_enqueue)

        assert enqueued[0].persona == "custom-persona"

    @pytest.mark.asyncio
    async def test_task_uses_configured_title(self) -> None:
        config = _make_config(task_description="My custom dream cycle")
        trigger = _make_trigger(config=config)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED):
            await trigger._poll_once(_enqueue)

        assert enqueued[0].title == "My custom dream cycle"

    @pytest.mark.asyncio
    async def test_task_context_includes_last_dream_at_when_set(self) -> None:
        trigger = _make_trigger()
        trigger._last_dream_at = datetime(2026, 4, 11, 3, 0, tzinfo=UTC)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED):
            await trigger._poll_once(_enqueue)

        context = enqueued[0].initiative_context
        assert "2026-04-11" in context

    @pytest.mark.asyncio
    async def test_task_context_shows_beginning_when_no_last_run(self) -> None:
        trigger = _make_trigger()
        trigger._last_dream_at = None
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED):
            await trigger._poll_once(_enqueue)

        context = enqueued[0].initiative_context
        assert "the beginning" in context

    @pytest.mark.asyncio
    async def test_task_context_includes_token_budget(self) -> None:
        config = _make_config(token_budget_usd=1.25)
        trigger = _make_trigger(config=config)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED):
            await trigger._poll_once(_enqueue)

        assert "$1.25" in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_task_context_includes_autonomy_policy(self) -> None:
        config = _make_config(
            autonomy_mode="yolo",
            environment_id="cluster-a",
            proposal_store_path="/tmp/proposals.json",
        )
        trigger = _make_trigger(config=config)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED):
            await trigger._poll_once(_enqueue)

        context = enqueued[0].initiative_context
        assert "Autonomy mode: yolo" in context
        assert "Environment: cluster-a" in context
        assert "Proposal store: /tmp/proposals.json" in context
        assert "call `skill_manage`" in context

    @pytest.mark.asyncio
    async def test_task_context_includes_all_seven_steps(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> bool:
            enqueued.append(task)
            return True

        with patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED):
            await trigger._poll_once(_enqueue)

        context = enqueued[0].initiative_context
        for step_num in range(1, 8):
            assert f"Step {step_num}" in context
        assert "Do not call `sleipnir_publish`" in context
        assert "pages_updated" in context
        assert "entities_created" in context
        assert "lint_fixes" in context

    @pytest.mark.asyncio
    async def test_last_dream_at_updated_after_enqueue(self) -> None:
        trigger = _make_trigger()
        assert trigger._last_dream_at is None

        async def _enqueue(task: AgentTask) -> bool:
            return True

        with (
            patch.object(trigger, "_scheduled_run", return_value=_SCHEDULED),
            patch.object(trigger, "_save_state"),
        ):
            await trigger._poll_once(_enqueue)

        assert trigger._last_dream_at is not None


class TestDreamCycleTriggerStatePersistence:
    def test_load_state_with_no_file_is_noop(self, tmp_path: Path) -> None:
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._load_state()
        assert trigger._last_dream_at is None

    def test_save_and_load_state_roundtrip(self, tmp_path: Path) -> None:
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._last_dream_at = datetime(2026, 4, 11, 3, 0, tzinfo=UTC)
        trigger._save_state()

        trigger2 = _make_trigger(state_dir=tmp_path)
        trigger2._load_state()
        assert trigger2._last_dream_at == datetime(2026, 4, 11, 3, 0, tzinfo=UTC)

    def test_save_state_creates_directory_if_missing(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "nested" / "dir"
        trigger = _make_trigger(state_dir=state_dir)
        trigger._last_dream_at = datetime(2026, 4, 12, 3, 0, tzinfo=UTC)
        trigger._save_state()
        assert (state_dir / "dream_cycle_state.json").exists()

    def test_load_state_with_corrupt_file_raises_with_remedy(self, tmp_path: Path) -> None:
        state_file = tmp_path / "dream_cycle_state.json"
        state_file.write_text("not valid json", encoding="utf-8")

        trigger = _make_trigger(state_dir=tmp_path)
        with pytest.raises(ValueError, match="delete it to restart the schedule"):
            trigger._load_state()

    def test_save_state_with_no_last_dream_at_writes_empty_dict(self, tmp_path: Path) -> None:
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._last_dream_at = None
        trigger._save_state()

        raw = json.loads((tmp_path / "dream_cycle_state.json").read_text())
        assert raw == {}


class TestDreamCycleTriggerRunCancellation:
    @pytest.mark.asyncio
    async def test_run_exits_on_cancellation(self) -> None:
        config = _make_config(poll_interval_seconds=1)
        trigger = _make_trigger(config=config)

        async def _enqueue(task: AgentTask) -> bool:
            return True

        with patch.object(trigger, "_poll_once", side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                await trigger.run(_enqueue)

    @pytest.mark.asyncio
    async def test_run_raises_a_poll_error(self) -> None:
        """A failed poll ends the trigger loudly instead of silently skipping dreams."""
        trigger = _make_trigger(config=_make_config(poll_interval_seconds=0))

        with patch.object(trigger, "_poll_once", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                await trigger.run(AsyncMock())


class TestDreamCycleTriggerEdgeCases:
    def test_save_state_raises_on_write_error(self, tmp_path: Path) -> None:
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._last_dream_at = datetime(2026, 4, 12, 3, 0, tzinfo=UTC)

        with patch(
            "ravn.adapters.triggers.dream_cycle.Path.write_text",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                trigger._save_state()


class TestDreamCycleTriggerConfig:
    def test_default_config_is_disabled(self) -> None:
        config = DreamCycleTriggerConfig()
        assert config.enabled is False

    def test_default_cron_is_3am_daily(self) -> None:
        config = DreamCycleTriggerConfig()
        assert config.cron_expression == "0 3 * * *"

    def test_default_persona_is_mimir_curator(self) -> None:
        config = DreamCycleTriggerConfig()
        assert config.persona == "mimir-curator"

    def test_default_token_budget_is_fifty_cents(self) -> None:
        config = DreamCycleTriggerConfig()
        assert config.token_budget_usd == 0.50
