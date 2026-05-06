"""Unit tests for DreamCycleTrigger."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ravn.adapters.triggers.dream_cycle import DreamCycleTrigger
from ravn.config import DreamCycleTriggerConfig
from ravn.domain.models import AgentTask, OutputMode


def _make_config(
    enabled: bool = True,
    cron_expression: str = "0 3 * * *",
    persona: str = "mimir-curator",
    task_description: str = "Nightly dream cycle: enrich, lint, cross-reference",
    token_budget_usd: float = 0.50,
    poll_interval_seconds: int = 60,
) -> DreamCycleTriggerConfig:
    return DreamCycleTriggerConfig(
        enabled=enabled,
        cron_expression=cron_expression,
        persona=persona,
        task_description=task_description,
        token_budget_usd=token_budget_usd,
        poll_interval_seconds=poll_interval_seconds,
    )


def _make_trigger(
    config: DreamCycleTriggerConfig | None = None,
    state_dir: Path | None = None,
) -> DreamCycleTrigger:
    if config is None:
        config = _make_config()
    return DreamCycleTrigger(config=config, state_dir=state_dir)


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


class TestDreamCycleTriggerIsDue:
    def test_is_due_returns_false_when_cron_not_matched(self) -> None:
        trigger = _make_trigger()
        now = datetime(2026, 4, 12, 10, 30, tzinfo=UTC)
        with patch(
            "ravn.adapters.triggers.cron._cron_matches",
            return_value=False,
        ):
            assert trigger._is_due(now) is False

    def test_is_due_returns_true_when_cron_matched(self) -> None:
        trigger = _make_trigger()
        now = datetime(2026, 4, 12, 3, 0, tzinfo=UTC)
        with patch(
            "ravn.adapters.triggers.cron._cron_matches",
            return_value=True,
        ):
            assert trigger._is_due(now) is True

    def test_is_due_uses_real_cron_matching_for_3am(self) -> None:
        """Verify "0 3 * * *" matches 03:00 and not 10:30."""
        trigger = _make_trigger(config=_make_config(cron_expression="0 3 * * *"))
        assert trigger._is_due(datetime(2026, 4, 12, 3, 0, tzinfo=UTC)) is True
        assert trigger._is_due(datetime(2026, 4, 12, 10, 30, tzinfo=UTC)) is False


class TestDreamCycleTriggerPollOnce:
    @pytest.mark.asyncio
    async def test_poll_once_skips_when_not_due(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=False):
            await trigger._poll_once(_enqueue)

        assert enqueued == []

    @pytest.mark.asyncio
    async def test_poll_once_enqueues_task_when_due(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 1
        task = enqueued[0]
        assert task.persona == "mimir-curator"
        assert task.triggered_by == "dream_cycle:cron"
        assert task.output_mode == OutputMode.SILENT
        assert "Dream cycle run" in task.initiative_context

    @pytest.mark.asyncio
    async def test_poll_once_deduplicates_within_same_minute(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            # First poll — should enqueue
            await trigger._poll_once(_enqueue)
            # Second poll in the same minute — should deduplicate
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 1

    @pytest.mark.asyncio
    async def test_poll_once_fires_again_in_next_minute(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        # Simulate a new minute by clearing the key
        trigger._last_cron_minute = ""

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 2


class TestDreamCycleTriggerTaskContent:
    @pytest.mark.asyncio
    async def test_task_uses_configured_persona(self) -> None:
        config = _make_config(persona="custom-persona")
        trigger = _make_trigger(config=config)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        assert enqueued[0].persona == "custom-persona"

    @pytest.mark.asyncio
    async def test_task_uses_configured_title(self) -> None:
        config = _make_config(task_description="My custom dream cycle")
        trigger = _make_trigger(config=config)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        assert enqueued[0].title == "My custom dream cycle"

    @pytest.mark.asyncio
    async def test_task_context_includes_last_dream_at_when_set(self) -> None:
        trigger = _make_trigger()
        trigger._last_dream_at = datetime(2026, 4, 11, 3, 0, tzinfo=UTC)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        context = enqueued[0].initiative_context
        assert "2026-04-11" in context

    @pytest.mark.asyncio
    async def test_task_context_shows_beginning_when_no_last_run(self) -> None:
        trigger = _make_trigger()
        trigger._last_dream_at = None
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        context = enqueued[0].initiative_context
        assert "the beginning" in context

    @pytest.mark.asyncio
    async def test_task_context_includes_token_budget(self) -> None:
        config = _make_config(token_budget_usd=1.25)
        trigger = _make_trigger(config=config)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        assert "$1.25" in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_task_context_includes_all_seven_steps(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with patch.object(trigger, "_is_due", return_value=True):
            await trigger._poll_once(_enqueue)

        context = enqueued[0].initiative_context
        for step_num in range(1, 8):
            assert f"Step {step_num}" in context

    @pytest.mark.asyncio
    async def test_last_dream_at_updated_after_enqueue(self) -> None:
        trigger = _make_trigger()
        assert trigger._last_dream_at is None

        async def _enqueue(task: AgentTask) -> None:
            pass

        with (
            patch.object(trigger, "_is_due", return_value=True),
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

    def test_load_state_with_corrupt_file_is_graceful(self, tmp_path: Path) -> None:
        state_file = tmp_path / "dream_cycle_state.json"
        state_file.write_text("not valid json", encoding="utf-8")

        trigger = _make_trigger(state_dir=tmp_path)
        trigger._load_state()  # should not raise
        assert trigger._last_dream_at is None

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

        async def _enqueue(task: AgentTask) -> None:
            pass

        with patch.object(trigger, "_poll_once", side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                await trigger.run(_enqueue)

    @pytest.mark.asyncio
    async def test_run_continues_after_poll_error(self) -> None:
        """A non-CancelledError poll exception should be swallowed (logged + continue)."""
        config = _make_config(poll_interval_seconds=0)
        trigger = _make_trigger(config=config)

        call_count = 0

        async def _flaky_poll(enqueue):  # type: ignore[override]
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient error")
            raise asyncio.CancelledError()

        with patch.object(trigger, "_poll_once", side_effect=_flaky_poll):
            with pytest.raises(asyncio.CancelledError):
                await trigger.run(AsyncMock())

        assert call_count >= 3


class TestDreamCycleTriggerEdgeCases:
    def test_is_due_returns_false_on_import_error(self) -> None:
        """When the cron module can't be imported, _is_due returns False gracefully."""
        import sys

        trigger = _make_trigger()
        now = datetime(2026, 4, 12, 3, 0, tzinfo=UTC)

        # Temporarily hide the cron module to trigger ImportError
        original = sys.modules.get("ravn.adapters.triggers.cron")
        sys.modules["ravn.adapters.triggers.cron"] = None  # type: ignore[assignment]
        try:
            result = trigger._is_due(now)
        finally:
            if original is None:
                sys.modules.pop("ravn.adapters.triggers.cron", None)
            else:
                sys.modules["ravn.adapters.triggers.cron"] = original

        assert result is False

    def test_save_state_is_graceful_on_write_error(self, tmp_path: Path) -> None:
        """A write failure in _save_state should be logged, not raised."""
        trigger = _make_trigger(state_dir=tmp_path)
        trigger._last_dream_at = datetime(2026, 4, 12, 3, 0, tzinfo=UTC)

        with patch(
            "ravn.adapters.triggers.dream_cycle.Path.write_text",
            side_effect=OSError("disk full"),
        ):
            trigger._save_state()  # should not raise


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
