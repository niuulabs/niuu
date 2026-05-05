"""Tests for DriveLoop, SilentChannel, triggers, and initiative prompt."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.channels.silent import SilentChannel
from ravn.adapters.triggers.condition_poll import ConditionPollTrigger
from ravn.adapters.triggers.cron import CronJob, CronTrigger, _cron_matches, parse_schedule
from ravn.config import InitiativeConfig, Settings, TriggerAdapterConfig
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import AgentTask, OutputMode
from ravn.drive_loop import DriveLoop
from ravn.prompt_builder import build_initiative_prompt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    priority: int = 10,
    output_mode: OutputMode = OutputMode.SILENT,
    deadline: datetime | None = None,
) -> AgentTask:
    hex_ts = hex(int(time.time() * 1000))[2:]
    return AgentTask(
        task_id=f"task_{hex_ts}_0001",
        title="test task",
        initiative_context="do something useful",
        triggered_by="cron:test",
        output_mode=output_mode,
        priority=priority,
        deadline=deadline,
    )


def _make_initiative_config(**kwargs) -> InitiativeConfig:
    defaults = {
        "enabled": True,
        "max_concurrent_tasks": 2,
        "task_queue_max": 10,
        "queue_journal_path": "/tmp/ravn_test_queue.json",
        "default_output_mode": "silent",
    }
    defaults.update(kwargs)
    return InitiativeConfig(**defaults)


def _make_drive_loop(tmp_path: Path) -> tuple[DriveLoop, MagicMock]:
    journal = tmp_path / "queue.json"
    config = _make_initiative_config(queue_journal_path=str(journal))
    settings = Settings()
    factory = MagicMock(return_value=MagicMock())
    loop = DriveLoop(agent_factory=factory, config=config, settings=settings)
    return loop, factory


# ---------------------------------------------------------------------------
# OutputMode enum
# ---------------------------------------------------------------------------


def test_output_mode_values() -> None:
    assert OutputMode.SILENT == "silent"
    assert OutputMode.AMBIENT == "ambient"
    assert OutputMode.SURFACE == "surface"


# ---------------------------------------------------------------------------
# AgentTask
# ---------------------------------------------------------------------------


def test_agent_task_session_id_auto_generated() -> None:
    task = _make_task()
    assert task.session_id == f"daemon_{task.task_id}"


def test_agent_task_created_at_defaults_to_utc_now() -> None:
    before = datetime.now(UTC)
    task = _make_task()
    after = datetime.now(UTC)
    assert before <= task.created_at <= after


# ---------------------------------------------------------------------------
# SilentChannel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_channel_discards_non_response_events() -> None:
    channel = SilentChannel()
    event = RavnEvent.thought("agent", "thinking...", "c1", "s1")
    await channel.emit(event)
    assert channel.response_text == ""
    assert not channel.surface_triggered


@pytest.mark.asyncio
async def test_silent_channel_captures_response_text() -> None:
    channel = SilentChannel()
    event = RavnEvent.response("agent", "All systems normal.", "c1", "s1")
    await channel.emit(event)
    assert channel.response_text == "All systems normal."
    assert not channel.surface_triggered


@pytest.mark.asyncio
async def test_silent_channel_detects_surface_prefix() -> None:
    channel = SilentChannel()
    event = RavnEvent.response("agent", "[SURFACE] Something needs attention!", "c1", "s1")
    await channel.emit(event)
    assert channel.surface_triggered
    assert channel.response_text.startswith("[SURFACE]")


@pytest.mark.asyncio
async def test_silent_channel_no_surface_for_bare_response() -> None:
    channel = SilentChannel()
    event = RavnEvent.response("agent", "Everything is fine.", "c1", "s1")
    await channel.emit(event)
    assert not channel.surface_triggered


# ---------------------------------------------------------------------------
# build_initiative_prompt
# ---------------------------------------------------------------------------


def test_build_initiative_prompt_contains_trigger() -> None:
    task = _make_task()
    prompt = build_initiative_prompt(task)
    assert task.triggered_by in prompt
    assert task.title in prompt
    assert task.initiative_context in prompt


def test_build_initiative_prompt_contains_surface_instruction() -> None:
    task = _make_task()
    prompt = build_initiative_prompt(task)
    assert "[SURFACE]" in prompt
    assert "No human sent this message" in prompt


# ---------------------------------------------------------------------------
# DriveLoop — enqueue / priority ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_enqueue_respects_priority(tmp_path: Path) -> None:
    loop, _ = _make_drive_loop(tmp_path)
    high = _make_task(priority=1)
    low = _make_task(priority=20)

    await loop.enqueue(low)
    await loop.enqueue(high)

    # First item dequeued should be the higher-priority task (lower number)
    prio1, _, task1 = await loop._queue.get()
    prio2, _, task2 = await loop._queue.get()
    assert prio1 < prio2
    assert task1.task_id == high.task_id
    assert task2.task_id == low.task_id


@pytest.mark.asyncio
async def test_drive_loop_enqueue_discards_expired_deadline(tmp_path: Path) -> None:
    loop, _ = _make_drive_loop(tmp_path)
    past = datetime.now(UTC) - timedelta(hours=1)
    task = _make_task(deadline=past)

    await loop.enqueue(task)
    assert loop._queue.empty()


@pytest.mark.asyncio
async def test_drive_loop_enqueue_respects_queue_max(tmp_path: Path) -> None:
    journal = tmp_path / "queue.json"
    config = _make_initiative_config(task_queue_max=2, queue_journal_path=str(journal))
    settings = Settings()
    factory = MagicMock()
    loop = DriveLoop(agent_factory=factory, config=config, settings=settings)

    for _ in range(3):
        await loop.enqueue(_make_task())

    assert loop._queue.qsize() == 2


# ---------------------------------------------------------------------------
# DriveLoop — queue journal round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_journal_round_trip(tmp_path: Path) -> None:
    journal = tmp_path / "queue.json"
    config = _make_initiative_config(queue_journal_path=str(journal))
    settings = Settings()
    factory = MagicMock()

    loop1 = DriveLoop(agent_factory=factory, config=config, settings=settings)
    task = _make_task()
    await loop1.enqueue(task)

    assert journal.exists()
    raw = json.loads(journal.read_text())
    # journal format is {"queue": [...]} (new) or bare list (old)
    records = raw["queue"] if isinstance(raw, dict) else raw
    assert len(records) == 1
    assert records[0]["task_id"] == task.task_id

    # Simulate restart by creating a new DriveLoop and loading the journal
    loop2 = DriveLoop(agent_factory=factory, config=config, settings=settings)
    loop2._load_journal()
    assert not loop2._queue.empty()
    _, _, restored = await loop2._queue.get()
    assert restored.task_id == task.task_id


@pytest.mark.asyncio
async def test_drive_loop_journal_skips_expired_tasks(tmp_path: Path) -> None:
    journal = tmp_path / "queue.json"
    past = datetime.now(UTC) - timedelta(hours=1)

    # Write an expired task directly to the journal
    records = [
        {
            "task_id": "task_old_0001",
            "title": "expired",
            "initiative_context": "ctx",
            "triggered_by": "cron:x",
            "output_mode": "silent",
            "persona": None,
            "priority": 10,
            "max_tokens": None,
            "deadline": past.isoformat(),
            "created_at": (past - timedelta(minutes=5)).isoformat(),
        }
    ]
    journal.write_text(json.dumps(records))

    config = _make_initiative_config(queue_journal_path=str(journal))
    settings = Settings()
    loop = DriveLoop(agent_factory=MagicMock(), config=config, settings=settings)
    loop._load_journal()
    assert loop._queue.empty()


# ---------------------------------------------------------------------------
# DriveLoop — surface escalation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_surface_escalation_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    loop, factory = _make_drive_loop(tmp_path)

    # Agent that responds with [SURFACE]
    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)

    # Patch the SilentChannel to always have surface_triggered=True
    with patch("ravn.drive_loop.SilentChannel") as mock_channel:
        mock_ch = MagicMock()
        mock_ch.emit = AsyncMock()
        mock_ch.surface_triggered = True
        mock_ch.response_text = "[SURFACE] Disk at 90%"
        mock_channel.return_value = mock_ch
        factory.return_value = mock_agent

        task = _make_task(output_mode=OutputMode.SILENT)
        with caplog.at_level(logging.INFO, logger="ravn.drive_loop"):
            await loop._run_task(task)

    assert any("surface escalation" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# CronTrigger — schedule parsing
# ---------------------------------------------------------------------------


def test_parse_schedule_cron_expression() -> None:
    result = parse_schedule("0 8 * * *")
    assert result == "0 8 * * *"


def test_parse_schedule_natural_every_minutes() -> None:
    result = parse_schedule("every 30m")
    assert result == "every:1800"


def test_parse_schedule_natural_every_hours() -> None:
    result = parse_schedule("every 2h")
    assert result == "every:7200"


def test_parse_schedule_natural_every_seconds() -> None:
    result = parse_schedule("every 5s")
    assert result == "every:5"


def test_parse_schedule_daily_at() -> None:
    result = parse_schedule("daily at 08:00")
    assert result == "0 8 * * *"


def test_parse_schedule_iso_timestamp() -> None:
    result = parse_schedule("2026-04-07T08:00:00")
    assert result.startswith("once:")


def test_cron_matches_wildcard() -> None:
    # "* * * * *" matches any datetime
    dt = datetime(2026, 4, 7, 8, 30, tzinfo=UTC)
    assert _cron_matches("* * * * *", dt)


def test_cron_matches_specific_time() -> None:
    dt = datetime(2026, 4, 7, 8, 0, tzinfo=UTC)
    assert _cron_matches("0 8 * * *", dt)

    dt_wrong = datetime(2026, 4, 7, 9, 0, tzinfo=UTC)
    assert not _cron_matches("0 8 * * *", dt_wrong)


def test_cron_matches_weekday() -> None:
    # Cron convention: 0=Sun, 1=Mon, ..., 6=Sat; "1-5" = Mon-Fri
    monday = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)  # Monday
    saturday = datetime(2026, 4, 11, 8, 0, tzinfo=UTC)  # Saturday
    sunday = datetime(2026, 4, 5, 8, 0, tzinfo=UTC)  # Sunday

    assert _cron_matches("0 8 * * 1-5", monday)
    assert not _cron_matches("0 8 * * 1-5", saturday)
    assert not _cron_matches("0 8 * * 1-5", sunday)
    assert _cron_matches("0 8 * * 0", sunday)  # Sunday = 0 in cron


# ---------------------------------------------------------------------------
# CronTrigger — due detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_trigger_fires_on_schedule(tmp_path: Path) -> None:
    state_path = tmp_path / "cron_state.json"
    lock_path = tmp_path / "cron.lock"

    job = CronJob(
        name="test_job",
        schedule="every 1s",  # fires immediately on first tick
        context="check stuff",
        output_mode=OutputMode.SILENT,
    )
    trigger = CronTrigger(
        jobs=[job],
        state_path=state_path,
        lock_path=lock_path,
        tick_seconds=0.1,
    )

    enqueued: list[AgentTask] = []

    async def collect(task: AgentTask) -> None:
        enqueued.append(task)

    # Run for a short time then cancel
    try:
        await asyncio.wait_for(trigger.run(collect), timeout=0.5)
    except TimeoutError:
        pass

    assert len(enqueued) >= 1
    assert enqueued[0].triggered_by == "cron:test_job"


@pytest.mark.asyncio
async def test_cron_trigger_fires_once_for_one_shot(tmp_path: Path) -> None:
    state_path = tmp_path / "cron_state.json"
    lock_path = tmp_path / "cron.lock"

    # Set a past timestamp so it fires immediately
    past_iso = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job = CronJob(
        name="once_job",
        schedule=past_iso,
        context="one-shot",
        output_mode=OutputMode.AMBIENT,
    )
    trigger = CronTrigger(
        jobs=[job],
        state_path=state_path,
        lock_path=lock_path,
        tick_seconds=0.05,
    )

    enqueued: list[AgentTask] = []

    async def collect(task: AgentTask) -> None:
        enqueued.append(task)

    try:
        await asyncio.wait_for(trigger.run(collect), timeout=0.5)
    except TimeoutError:
        pass

    # Should fire exactly once
    assert len(enqueued) == 1
    assert enqueued[0].output_mode == OutputMode.AMBIENT


# ---------------------------------------------------------------------------
# ConditionPollTrigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_condition_poll_trigger_fires_on_trigger_verdict(tmp_path: Path) -> None:
    mock_agent = AsyncMock()

    def agent_factory() -> object:
        return mock_agent

    # Patch SilentChannel to return TRIGGER verdict
    with patch("ravn.adapters.triggers.condition_poll.SilentChannel") as mock_ch_cls:
        mock_ch = MagicMock()
        mock_ch.response_text = "TRIGGER"
        mock_ch_cls.return_value = mock_ch

        trigger = ConditionPollTrigger(
            name="disk_check",
            sensor_prompt="Is disk above 80%?",
            task_context="Investigate disk usage.",
            sensor_agent_factory=agent_factory,
            output_mode=OutputMode.AMBIENT,
            check_interval_seconds=0.05,
            cooldown_minutes=0,
        )

        enqueued: list[AgentTask] = []

        async def collect(task: AgentTask) -> None:
            enqueued.append(task)

        try:
            await asyncio.wait_for(trigger.run(collect), timeout=0.3)
        except TimeoutError:
            pass

    assert len(enqueued) >= 1
    assert enqueued[0].triggered_by == "condition:disk_check"


@pytest.mark.asyncio
async def test_condition_poll_trigger_does_not_fire_on_clear(tmp_path: Path) -> None:
    mock_agent = AsyncMock()

    def agent_factory() -> object:
        return mock_agent

    with patch("ravn.adapters.triggers.condition_poll.SilentChannel") as mock_ch_cls:
        mock_ch = MagicMock()
        mock_ch.response_text = "CLEAR"
        mock_ch_cls.return_value = mock_ch

        trigger = ConditionPollTrigger(
            name="cpu_check",
            sensor_prompt="Is CPU above 90%?",
            task_context="Investigate CPU usage.",
            sensor_agent_factory=agent_factory,
            output_mode=OutputMode.SILENT,
            check_interval_seconds=0.05,
            cooldown_minutes=0,
        )

        enqueued: list[AgentTask] = []

        async def collect(task: AgentTask) -> None:
            enqueued.append(task)

        try:
            await asyncio.wait_for(trigger.run(collect), timeout=0.3)
        except TimeoutError:
            pass

    assert len(enqueued) == 0


# ---------------------------------------------------------------------------
# TriggerPort protocol conformance
# ---------------------------------------------------------------------------


def test_cron_trigger_satisfies_trigger_port() -> None:
    from ravn.ports.trigger import TriggerPort

    job = CronJob("test", "* * * * *", "ctx", OutputMode.SILENT)
    trigger = CronTrigger(jobs=[job])
    assert isinstance(trigger, TriggerPort)


def test_silent_trigger_satisfies_trigger_port() -> None:
    from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger
    from ravn.ports.trigger import TriggerPort

    trigger = SleipnirEventTrigger(
        name="test",
        pattern="ravn.#",
        context_template="{{ payload.task }}",
    )
    assert isinstance(trigger, TriggerPort)


# ---------------------------------------------------------------------------
# RavnEventType — TASK_STARTED
# ---------------------------------------------------------------------------


def test_task_started_event_type_exists() -> None:
    assert RavnEventType.TASK_STARTED == "task_started"


def test_task_started_factory() -> None:
    event = RavnEvent.task_started(
        source="drive_loop",
        task_id="task_abc",
        title="morning check",
        correlation_id="task_abc",
        session_id="daemon_task_abc",
    )
    assert event.type == RavnEventType.TASK_STARTED
    assert event.payload["task_id"] == "task_abc"
    assert event.payload["title"] == "morning check"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_initiative_config_defaults() -> None:
    cfg = InitiativeConfig()
    assert not cfg.enabled
    assert cfg.max_concurrent_tasks == 3
    assert cfg.task_queue_max == 50


def test_trigger_adapter_config() -> None:
    ta = TriggerAdapterConfig(adapter="mypackage.MyTrigger", kwargs={"interval": 60})
    assert ta.adapter == "mypackage.MyTrigger"
    assert ta.kwargs["interval"] == 60
    assert ta.secret_kwargs_env == {}


def test_settings_has_initiative_field() -> None:
    settings = Settings()
    assert hasattr(settings, "initiative")
    assert isinstance(settings.initiative, InitiativeConfig)


# ---------------------------------------------------------------------------
# Additional coverage — cron field matching
# ---------------------------------------------------------------------------


def test_cron_field_matches_step() -> None:
    from ravn.adapters.triggers.cron import _field_matches

    # */5 — every 5 units
    assert _field_matches(0, "*/5")
    assert _field_matches(5, "*/5")
    assert not _field_matches(3, "*/5")


def test_cron_field_matches_range() -> None:
    from ravn.adapters.triggers.cron import _field_matches

    assert _field_matches(3, "1-5")
    assert not _field_matches(6, "1-5")


def test_cron_field_matches_list() -> None:
    from ravn.adapters.triggers.cron import _field_matches

    assert _field_matches(0, "0,15,30,45")
    assert _field_matches(30, "0,15,30,45")
    assert not _field_matches(7, "0,15,30,45")


def test_cron_field_matches_specific_value() -> None:
    from ravn.adapters.triggers.cron import _field_matches

    assert _field_matches(8, "8")
    assert not _field_matches(9, "8")


def test_cron_matches_invalid_expression() -> None:
    dt = datetime(2026, 4, 7, 8, 0, tzinfo=UTC)
    assert not _cron_matches("not a cron", dt)
    assert not _cron_matches("* * * *", dt)  # too few fields


def test_cron_field_matches_step_with_base() -> None:
    from ravn.adapters.triggers.cron import _field_matches

    # 2/3 — matches 2, 5, 8, ...
    assert _field_matches(2, "2/3")
    assert _field_matches(5, "2/3")
    assert not _field_matches(3, "2/3")


# ---------------------------------------------------------------------------
# Additional coverage — cron trigger state and due detection
# ---------------------------------------------------------------------------


def test_cron_trigger_not_due_within_minute(tmp_path: Path) -> None:
    from ravn.adapters.triggers.cron import CronTrigger

    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "lock"

    job = CronJob("job1", "* * * * *", "ctx", OutputMode.SILENT)
    trigger = CronTrigger(jobs=[job], state_path=state_path, lock_path=lock_path)

    now = datetime.now(UTC)
    state = {"job1": now.isoformat()}

    # Should not fire because last_fired was less than 60 seconds ago
    assert not trigger._is_due(job, now, state)


def test_cron_trigger_state_save_and_load(tmp_path: Path) -> None:
    from ravn.adapters.triggers.cron import CronTrigger

    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "lock"

    job = CronJob("job1", "* * * * *", "ctx", OutputMode.SILENT)
    trigger = CronTrigger(jobs=[job], state_path=state_path, lock_path=lock_path)

    now_iso = datetime.now(UTC).isoformat()
    trigger._save_state({"job1": now_iso})

    loaded = trigger._load_state()
    assert loaded == {"job1": now_iso}


def test_cron_trigger_load_state_missing_file(tmp_path: Path) -> None:
    from ravn.adapters.triggers.cron import CronTrigger

    trigger = CronTrigger(
        jobs=[],
        state_path=tmp_path / "nonexistent.json",
        lock_path=tmp_path / "lock",
    )
    assert trigger._load_state() == {}


def test_cron_trigger_every_interval_due_first_time(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "lock"

    job = CronJob("job2", "every 60s", "ctx", OutputMode.SILENT)
    trigger = CronTrigger(jobs=[job], state_path=state_path, lock_path=lock_path)

    now = datetime.now(UTC)
    # No previous fire — should be due
    assert trigger._is_due(job, now, {})


def test_cron_trigger_every_interval_not_due_yet(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "lock"

    job = CronJob("job3", "every 3600s", "ctx", OutputMode.SILENT)
    trigger = CronTrigger(jobs=[job], state_path=state_path, lock_path=lock_path)

    now = datetime.now(UTC)
    recently = (now - timedelta(seconds=10)).isoformat()
    assert not trigger._is_due(job, now, {"job3": recently})


def test_cron_trigger_once_not_yet_fired(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "lock"

    future_iso = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    job = CronJob("future_job", future_iso, "ctx", OutputMode.SILENT)
    trigger = CronTrigger(jobs=[job], state_path=state_path, lock_path=lock_path)

    now = datetime.now(UTC)
    assert not trigger._is_due(job, now, {})


def test_cron_trigger_task_id_generation(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "lock"

    trigger = CronTrigger(jobs=[], state_path=state_path, lock_path=lock_path)
    id1 = trigger._make_task_id()
    id2 = trigger._make_task_id()

    assert id1.startswith("task_")
    assert id2.startswith("task_")
    assert id1 != id2


# ---------------------------------------------------------------------------
# Additional coverage — condition poll cooldown and errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_condition_poll_cooldown_prevents_second_fire() -> None:
    mock_agent = AsyncMock()

    def agent_factory() -> object:
        return mock_agent

    with patch("ravn.adapters.triggers.condition_poll.SilentChannel") as mock_ch_cls:
        mock_ch = MagicMock()
        mock_ch.response_text = "TRIGGER"
        mock_ch_cls.return_value = mock_ch

        trigger = ConditionPollTrigger(
            name="cool_check",
            sensor_prompt="status?",
            task_context="investigate",
            sensor_agent_factory=agent_factory,
            output_mode=OutputMode.SILENT,
            check_interval_seconds=0.05,
            cooldown_minutes=60,  # long cooldown
        )

        enqueued: list[AgentTask] = []

        async def collect(task: AgentTask) -> None:
            enqueued.append(task)

        # Run long enough for two poll cycles
        try:
            await asyncio.wait_for(trigger.run(collect), timeout=0.5)
        except TimeoutError:
            pass

    # Should fire once then be in cooldown
    assert len(enqueued) == 1


def test_condition_poll_not_in_cooldown_initially() -> None:
    trigger = ConditionPollTrigger(
        name="x",
        sensor_prompt="y",
        task_context="z",
        sensor_agent_factory=lambda: None,
    )
    assert not trigger._in_cooldown()


def test_condition_poll_in_cooldown_after_fire() -> None:
    trigger = ConditionPollTrigger(
        name="x",
        sensor_prompt="y",
        task_context="z",
        sensor_agent_factory=lambda: None,
        cooldown_minutes=60,
    )
    trigger._last_trigger_at = datetime.now(UTC)
    assert trigger._in_cooldown()


def test_condition_poll_name_property() -> None:
    trigger = ConditionPollTrigger(
        name="disk_check",
        sensor_prompt="y",
        task_context="z",
        sensor_agent_factory=lambda: None,
    )
    assert trigger.name == "condition_poll:disk_check"


@pytest.mark.asyncio
async def test_condition_poll_run_sensor_exception_returns_clear() -> None:
    """_run_sensor() returns CLEAR when agent.run_turn() raises."""
    mock_agent = AsyncMock()
    mock_agent.run_turn.side_effect = RuntimeError("sensor boom")

    trigger = ConditionPollTrigger(
        name="err_check",
        sensor_prompt="status?",
        task_context="investigate",
        sensor_agent_factory=lambda: mock_agent,
    )
    result = await trigger._run_sensor()
    assert result == "CLEAR"


@pytest.mark.asyncio
async def test_condition_poll_run_cancelled_error_exits() -> None:
    """CancelledError inside run() causes the loop to return."""
    mock_agent = AsyncMock()

    call_count = 0

    async def fake_run_sensor() -> str:
        nonlocal call_count
        call_count += 1
        raise asyncio.CancelledError

    trigger = ConditionPollTrigger(
        name="cancel_check",
        sensor_prompt="y",
        task_context="z",
        sensor_agent_factory=lambda: mock_agent,
        check_interval_seconds=0.01,
    )
    trigger._run_sensor = fake_run_sensor  # type: ignore[method-assign]

    enqueued: list = []

    async def collect(task) -> None:
        enqueued.append(task)

    # Should return without raising, because CancelledError is caught
    await asyncio.wait_for(trigger.run(collect), timeout=0.5)
    assert len(enqueued) == 0


@pytest.mark.asyncio
async def test_condition_poll_run_sensor_exception_continues_loop() -> None:
    """Generic exception from _run_sensor() logs a warning and continues."""
    mock_agent = AsyncMock()

    call_count = 0

    async def fake_run_sensor() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient failure")
        return "CLEAR"

    trigger = ConditionPollTrigger(
        name="retry_check",
        sensor_prompt="y",
        task_context="z",
        sensor_agent_factory=lambda: mock_agent,
        check_interval_seconds=0.01,
    )
    trigger._run_sensor = fake_run_sensor  # type: ignore[method-assign]

    enqueued: list = []

    async def collect(task) -> None:
        enqueued.append(task)

    try:
        await asyncio.wait_for(trigger.run(collect), timeout=0.2)
    except TimeoutError:
        pass

    # loop continued after exception — no tasks (CLEAR verdict)
    assert len(enqueued) == 0
    assert call_count >= 2


# ---------------------------------------------------------------------------
# SleipnirEventTrigger — aio_pika not installed path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sleipnir_trigger_disabled_without_aio_pika(caplog) -> None:
    import logging

    from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger

    trigger = SleipnirEventTrigger(
        name="test",
        pattern="ravn.#",
        context_template="{{ payload.task }}",
    )

    enqueued = []

    async def collect(task):
        enqueued.append(task)

    with patch.dict("sys.modules", {"aio_pika": None}):
        with caplog.at_level(logging.WARNING, logger="ravn.adapters.triggers.sleipnir"):
            await trigger.run(collect)

    # Should exit immediately without enqueuing anything
    assert len(enqueued) == 0


# ---------------------------------------------------------------------------
# SleipnirEventTrigger — non-network paths
# ---------------------------------------------------------------------------


def test_sleipnir_trigger_name() -> None:
    from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger

    trigger = SleipnirEventTrigger(name="my_trigger", pattern="ravn.#", context_template="ctx")
    assert trigger.name == "sleipnir_event:my_trigger"


def test_sleipnir_task_id_unique() -> None:
    from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger

    trigger = SleipnirEventTrigger(name="t", pattern="p", context_template="c")
    id1 = trigger._make_task_id()
    id2 = trigger._make_task_id()
    assert id1.startswith("task_")
    assert id1 != id2


def test_sleipnir_render_context_no_jinja2() -> None:
    from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger

    trigger = SleipnirEventTrigger(
        name="t", pattern="p", context_template="raw template {{ payload.task }}"
    )

    with patch.dict("sys.modules", {"jinja2": None}):
        result = trigger._render_context({"task": "check logs"})

    # When jinja2 unavailable, returns raw template
    assert result == "raw template {{ payload.task }}"


def test_sleipnir_render_context_with_jinja2() -> None:
    from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger

    # Only run if jinja2 is available
    try:
        import jinja2  # noqa: F401
    except ImportError:
        pytest.skip("jinja2 not installed")

    trigger = SleipnirEventTrigger(
        name="t", pattern="p", context_template="Task: {{ payload.task }}"
    )
    result = trigger._render_context({"task": "investigate alerts"})
    assert result == "Task: investigate alerts"


@pytest.mark.asyncio
async def test_sleipnir_run_with_mocked_aio_pika() -> None:
    """Test that run() enters the retry loop when aio_pika is available."""
    from ravn.adapters.triggers.sleipnir import SleipnirEventTrigger

    trigger = SleipnirEventTrigger(
        name="t",
        pattern="ravn.#",
        context_template="ctx",
    )

    enqueued: list = []

    async def collect(task):
        enqueued.append(task)

    # Mock aio_pika to raise immediately so the while loop gets one iteration
    mock_pika = MagicMock()
    mock_pika.connect_robust = AsyncMock(side_effect=ConnectionError("no broker"))

    with patch.dict("sys.modules", {"aio_pika": mock_pika}):
        try:
            await asyncio.wait_for(trigger.run(collect), timeout=0.2)
        except TimeoutError:
            pass

    # No tasks enqueued since connection failed
    assert len(enqueued) == 0


# ---------------------------------------------------------------------------
# DriveLoop — cancel running task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_cancel_unknown_task_is_no_op(tmp_path: Path) -> None:
    loop, _ = _make_drive_loop(tmp_path)
    # Should not raise
    await loop.cancel("nonexistent_task_id")


# ---------------------------------------------------------------------------
# DriveLoop — integration: cron trigger fires run_turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_loop_runs_agent_turn_from_cron(tmp_path: Path) -> None:
    """Integration test: cron trigger fires → run_turn called with initiative prompt."""
    journal = tmp_path / "queue.json"
    state_path = tmp_path / "cron_state.json"
    lock_path = tmp_path / "cron.lock"

    config = _make_initiative_config(
        max_concurrent_tasks=1,
        queue_journal_path=str(journal),
    )
    settings = Settings()

    turn_inputs: list[str] = []
    mock_agent = AsyncMock()

    async def _capture_run_turn(prompt: str) -> None:
        turn_inputs.append(prompt)

    mock_agent.run_turn = _capture_run_turn

    def agent_factory(channel, task_id=None, persona=None, triggered_by=None):
        return mock_agent

    drive_loop = DriveLoop(agent_factory=agent_factory, config=config, settings=settings)

    job = CronJob(
        name="integration_test",
        schedule="every 1s",
        context="run integration check",
        output_mode=OutputMode.SILENT,
    )
    cron = CronTrigger(jobs=[job], state_path=state_path, lock_path=lock_path, tick_seconds=0.05)
    drive_loop.register_trigger(cron)

    try:
        await asyncio.wait_for(drive_loop.run(), timeout=1.0)
    except TimeoutError:
        pass
    except asyncio.CancelledError:
        pass

    assert len(turn_inputs) >= 1
    assert "INITIATIVE TASK" in turn_inputs[0]
    assert "integration_test" in turn_inputs[0]
    assert "run integration check" in turn_inputs[0]


# ---------------------------------------------------------------------------
# DriveLoop — thread lifecycle (_finalise_thread)
# ---------------------------------------------------------------------------


def _make_thread_task(path: str = "threads/test-thread") -> AgentTask:
    hex_ts = hex(int(time.time() * 1000))[2:]
    return AgentTask(
        task_id=f"task_{hex_ts}_thread",
        title="thread task",
        initiative_context="work on thread",
        triggered_by=f"thread:{path}",
        output_mode=OutputMode.AMBIENT,
        priority=5,
    )


def _make_drive_loop_with_mimir(tmp_path: Path) -> tuple[DriveLoop, MagicMock, AsyncMock]:
    journal = tmp_path / "queue.json"
    config = _make_initiative_config(queue_journal_path=str(journal))
    settings = Settings()
    factory = MagicMock(return_value=MagicMock())
    mock_mimir = AsyncMock()
    mock_mimir.update_thread_state = AsyncMock()
    mock_mimir.assign_thread_owner = AsyncMock()
    loop = DriveLoop(agent_factory=factory, config=config, settings=settings, mimir=mock_mimir)
    return loop, factory, mock_mimir


@pytest.mark.asyncio
async def test_finalise_thread_success_closes_thread(tmp_path: Path) -> None:
    """Thread-triggered task success → state becomes closed."""
    from niuu.domain.mimir import ThreadState

    loop, factory, mock_mimir = _make_drive_loop_with_mimir(tmp_path)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)
    factory.return_value = mock_agent

    task = _make_thread_task("threads/my-thread")
    await loop._run_task(task)

    mock_mimir.update_thread_state.assert_awaited_once_with("threads/my-thread", ThreadState.closed)
    mock_mimir.assign_thread_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalise_thread_failure_reopens_and_releases(tmp_path: Path) -> None:
    """Thread-triggered task failure → state returns to open, ownership released."""
    from niuu.domain.mimir import ThreadState

    loop, factory, mock_mimir = _make_drive_loop_with_mimir(tmp_path)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(side_effect=RuntimeError("agent exploded"))
    factory.return_value = mock_agent

    task = _make_thread_task("threads/failing-thread")
    await loop._run_task(task)

    mock_mimir.update_thread_state.assert_awaited_once_with(
        "threads/failing-thread", ThreadState.open
    )
    mock_mimir.assign_thread_owner.assert_awaited_once_with("threads/failing-thread", None)


@pytest.mark.asyncio
async def test_finalise_thread_skipped_for_non_thread_task(tmp_path: Path) -> None:
    """Non-thread task (cron, event) → no thread lifecycle logic runs."""
    loop, factory, mock_mimir = _make_drive_loop_with_mimir(tmp_path)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)
    factory.return_value = mock_agent

    task = _make_task()  # triggered_by="cron:test"
    await loop._run_task(task)

    mock_mimir.update_thread_state.assert_not_awaited()
    mock_mimir.assign_thread_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalise_thread_mimir_error_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Mímir errors during finalisation → logged, not propagated."""
    import logging

    loop, factory, mock_mimir = _make_drive_loop_with_mimir(tmp_path)
    mock_mimir.update_thread_state = AsyncMock(side_effect=Exception("mimir down"))

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)
    factory.return_value = mock_agent

    task = _make_thread_task("threads/error-thread")
    with caplog.at_level(logging.WARNING, logger="ravn.drive_loop"):
        await loop._run_task(task)  # must not raise

    assert any("failed to finalise thread" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_finalise_thread_no_mimir_skips_gracefully(tmp_path: Path) -> None:
    """Drive loop without mimir (None) → finalisation skipped gracefully."""
    loop, factory = _make_drive_loop(tmp_path)  # no mimir

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(return_value=None)
    factory.return_value = mock_agent

    task = _make_thread_task("threads/no-mimir-thread")
    # Should complete without error even though mimir is None
    await loop._run_task(task)


@pytest.mark.asyncio
async def test_finalise_thread_on_cancelled_task(tmp_path: Path) -> None:
    """Cancelled thread task → state returns to open, ownership released."""
    from niuu.domain.mimir import ThreadState

    loop, factory, mock_mimir = _make_drive_loop_with_mimir(tmp_path)

    mock_agent = AsyncMock()
    mock_agent.run_turn = AsyncMock(side_effect=asyncio.CancelledError())
    factory.return_value = mock_agent

    task = _make_thread_task("threads/cancelled-thread")
    await loop._run_task(task)

    mock_mimir.update_thread_state.assert_awaited_once_with(
        "threads/cancelled-thread", ThreadState.open
    )
    mock_mimir.assign_thread_owner.assert_awaited_once_with("threads/cancelled-thread", None)
