"""Tests for NIU-437 cron scheduling: CronJobStore, cron tools, CronTrigger."""

from __future__ import annotations

import asyncio
import stat
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ravn.adapters.tools.cron_tools import (
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    build_cron_tools,
)
from ravn.adapters.triggers.cron import (
    _DELIVERY_TO_OUTPUT_MODE,
    _OUTPUT_BASE,
    _SILENT_MARKER,
    CronJob,
    CronJobRecord,
    CronJobStore,
    CronTrigger,
    _cron_matches,
    _field_matches,
    make_cron_trigger,
    parse_schedule,
)
from ravn.domain.models import AgentTask, OutputMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> CronJobStore:
    return CronJobStore(jobs_path=tmp_path / "jobs.json")


def _make_record(**kwargs) -> CronJobRecord:
    defaults = {
        "job_id": "aabbccdd11223344",
        "name": "test-job",
        "schedule": "every 5m",
        "context": "Run a quick sanity check.",
        "delivery": "local",
        "persona": None,
        "priority": 10,
    }
    defaults.update(kwargs)
    return CronJobRecord(**defaults)


# ---------------------------------------------------------------------------
# parse_schedule
# ---------------------------------------------------------------------------


class TestParseSchedule:
    def test_every_minutes(self):
        assert parse_schedule("every 30m") == "every:1800"

    def test_every_seconds(self):
        assert parse_schedule("every 5s") == "every:5"

    def test_every_hours(self):
        assert parse_schedule("every 2h") == "every:7200"

    def test_bare_interval_minutes(self):
        assert parse_schedule("30m") == "every:1800"

    def test_bare_interval_hours(self):
        assert parse_schedule("2h") == "every:7200"

    def test_bare_interval_seconds(self):
        assert parse_schedule("45s") == "every:45"

    def test_daily_at(self):
        result = parse_schedule("daily at 09:00")
        assert result == "0 9 * * *"

    def test_daily_at_midnight(self):
        result = parse_schedule("daily at 00:30")
        assert result == "30 0 * * *"

    def test_cron_passthrough(self):
        assert parse_schedule("0 8 * * *") == "0 8 * * *"

    def test_iso_timestamp(self):
        result = parse_schedule("2026-04-08T09:00:00")
        assert result.startswith("once:")

    def test_unknown_passthrough(self):
        result = parse_schedule("*/5 * * * *")
        assert result == "*/5 * * * *"


# ---------------------------------------------------------------------------
# _field_matches / _cron_matches
# ---------------------------------------------------------------------------


class TestCronParsing:
    def test_wildcard(self):
        assert _field_matches(5, "*") is True

    def test_exact(self):
        assert _field_matches(5, "5") is True
        assert _field_matches(5, "6") is False

    def test_range(self):
        assert _field_matches(5, "3-7") is True
        assert _field_matches(9, "3-7") is False

    def test_step(self):
        assert _field_matches(10, "*/5") is True
        assert _field_matches(11, "*/5") is False

    def test_list(self):
        assert _field_matches(3, "1,3,5") is True
        assert _field_matches(4, "1,3,5") is False

    def test_cron_matches_at_9am(self):
        dt = datetime(2026, 4, 8, 9, 0, 0, tzinfo=UTC)
        assert _cron_matches("0 9 * * *", dt) is True

    def test_cron_no_match(self):
        dt = datetime(2026, 4, 8, 10, 0, 0, tzinfo=UTC)
        assert _cron_matches("0 9 * * *", dt) is False

    def test_cron_every_5min(self):
        dt = datetime(2026, 4, 8, 9, 15, 0, tzinfo=UTC)
        assert _cron_matches("*/5 * * * *", dt) is True

    def test_bad_cron_expression(self):
        assert _cron_matches("not-valid", datetime.now(UTC)) is False


# ---------------------------------------------------------------------------
# CronJobStore
# ---------------------------------------------------------------------------


class TestCronJobStore:
    def test_empty_list(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.list() == []

    def test_create_and_list(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record()
        store.create(rec)

        jobs = store.list()
        assert len(jobs) == 1
        assert jobs[0].job_id == rec.job_id
        assert jobs[0].name == rec.name

    def test_file_permissions(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record()
        store.create(rec)

        mode = stat.S_IMODE((tmp_path / "jobs.json").stat().st_mode)
        assert mode == 0o600

    def test_get_existing(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record(job_id="findme00")
        store.create(rec)

        found = store.get("findme00")
        assert found is not None
        assert found.name == rec.name

    def test_get_missing(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get("nonexistent") is None

    def test_delete_existing(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record(job_id="deleteme0")
        store.create(rec)

        removed = store.delete("deleteme0")
        assert removed is True
        assert store.list() == []

    def test_delete_missing(self, tmp_path):
        store = _make_store(tmp_path)
        removed = store.delete("ghost")
        assert removed is False

    def test_duplicate_job_id_raises(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record(job_id="dupeid000")
        store.create(rec)
        with pytest.raises(ValueError, match="already exists"):
            store.create(_make_record(job_id="dupeid000"))

    def test_multiple_jobs(self, tmp_path):
        store = _make_store(tmp_path)
        for i in range(3):
            store.create(_make_record(job_id=f"job{i:014d}", name=f"job-{i}"))

        assert len(store.list()) == 3

    def test_corrupt_json_returns_empty(self, tmp_path):
        jobs_path = tmp_path / "jobs.json"
        jobs_path.write_text("not-json")
        store = CronJobStore(jobs_path=jobs_path)
        assert store.list() == []


# ---------------------------------------------------------------------------
# CronJobRecord serialisation
# ---------------------------------------------------------------------------


class TestCronJobRecord:
    def test_roundtrip(self):
        rec = _make_record(persona="assistant", delivery="sleipnir")
        d = rec.to_dict()
        restored = CronJobRecord.from_dict(d)
        assert restored.job_id == rec.job_id
        assert restored.delivery == "sleipnir"
        assert restored.persona == "assistant"

    def test_defaults_from_dict(self):
        rec = CronJobRecord.from_dict(
            {"job_id": "x", "name": "n", "schedule": "1m", "context": "c"}
        )
        assert rec.delivery == "local"
        assert rec.priority == 10
        assert rec.enabled is True


# ---------------------------------------------------------------------------
# CronTrigger._is_due_canonical
# ---------------------------------------------------------------------------


class TestCronTriggerIsDue:
    def _trigger(self):
        return CronTrigger(jobs=[])

    def test_once_not_yet(self):
        t = self._trigger()
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        assert t._is_due_canonical(f"once:{future}", "k", datetime.now(UTC), {}) is False

    def test_once_past_fires_once(self):
        t = self._trigger()
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        state: dict = {}
        assert t._is_due_canonical(f"once:{past}", "k", datetime.now(UTC), state) is True
        # Firing again with key in state → should not fire
        state["k"] = datetime.now(UTC).isoformat()
        assert t._is_due_canonical(f"once:{past}", "k", datetime.now(UTC), state) is False

    def test_every_first_time(self):
        t = self._trigger()
        assert t._is_due_canonical("every:60", "k", datetime.now(UTC), {}) is True

    def test_every_too_soon(self):
        t = self._trigger()
        state = {"k": datetime.now(UTC).isoformat()}
        assert t._is_due_canonical("every:3600", "k", datetime.now(UTC), state) is False

    def test_every_interval_elapsed(self):
        t = self._trigger()
        past = (datetime.now(UTC) - timedelta(seconds=3601)).isoformat()
        state = {"k": past}
        assert t._is_due_canonical("every:3600", "k", datetime.now(UTC), state) is True

    def test_cron_expr_no_last(self):
        t = self._trigger()
        now = datetime(2026, 4, 8, 9, 0, 0, tzinfo=UTC)
        assert t._is_due_canonical("0 9 * * *", "k", now, {}) is True

    def test_cron_expr_fired_recently(self):
        t = self._trigger()
        now = datetime(2026, 4, 8, 9, 0, 30, tzinfo=UTC)
        state = {"k": (now - timedelta(seconds=29)).isoformat()}
        assert t._is_due_canonical("0 9 * * *", "k", now, state) is False


# ---------------------------------------------------------------------------
# CronTrigger.run — store jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_trigger_fires_store_job(tmp_path):
    """A store-defined job is enqueued when due."""

    store = _make_store(tmp_path)
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "cron.lock"

    rec = _make_record(job_id="storejob00", name="store-job", schedule="every 1s")
    store.create(rec)

    trigger = CronTrigger(
        jobs=[],
        state_path=state_path,
        lock_path=lock_path,
        tick_seconds=9999,
        store=store,
    )

    enqueued: list[AgentTask] = []

    async def fake_enqueue(task: AgentTask) -> None:
        enqueued.append(task)

    # Simulate the store-job loop body from CronTrigger.run
    now = datetime.now(UTC)
    state: dict = {}

    for record in store.list():
        if not record.enabled:
            continue
        canonical = parse_schedule(record.schedule)
        if not trigger._is_due_canonical(canonical, record.job_id, now, state):
            continue
        context = record.context
        output_mode = _DELIVERY_TO_OUTPUT_MODE.get(record.delivery, OutputMode.SILENT)
        if context.startswith(_SILENT_MARKER):
            context = context[len(_SILENT_MARKER) :].strip()
            output_mode = OutputMode.SILENT
        timestamp_str = now.strftime("%Y%m%dT%H%M%S")
        output_path = _OUTPUT_BASE / record.job_id / f"{timestamp_str}.md"
        task_id = trigger._make_task_id()
        task = AgentTask(
            task_id=task_id,
            title=record.name,
            initiative_context=context,
            triggered_by=f"cron:{record.job_id}",
            output_mode=output_mode,
            persona=record.persona or None,
            priority=record.priority,
            output_path=output_path,
        )
        await fake_enqueue(task)
        state[record.job_id] = now.isoformat()

    assert len(enqueued) == 1
    task = enqueued[0]
    assert task.triggered_by == f"cron:{rec.job_id}"
    assert task.output_path is not None
    assert rec.job_id in str(task.output_path)


@pytest.mark.asyncio
async def test_cron_trigger_silent_marker(tmp_path):
    """[SILENT] prefix forces output_mode=SILENT and is stripped from context."""
    store = _make_store(tmp_path)
    rec = _make_record(
        job_id="silent0001",
        context="[SILENT] Check disk usage",
        delivery="platform",
    )
    store.create(rec)

    trigger = CronTrigger(jobs=[], store=store)
    enqueued: list[AgentTask] = []
    now = datetime.now(UTC)
    state: dict = {}

    for record in store.list():
        canonical = parse_schedule(record.schedule)
        if not trigger._is_due_canonical(canonical, record.job_id, now, state):
            continue
        context = record.context
        output_mode = _DELIVERY_TO_OUTPUT_MODE.get(record.delivery, OutputMode.SILENT)
        if context.startswith(_SILENT_MARKER):
            context = context[len(_SILENT_MARKER) :].strip()
            output_mode = OutputMode.SILENT
        output_path = _OUTPUT_BASE / record.job_id / f"{now.strftime('%Y%m%dT%H%M%S')}.md"
        task = AgentTask(
            task_id="t001",
            title=record.name,
            initiative_context=context,
            triggered_by=f"cron:{record.job_id}",
            output_mode=output_mode,
            persona=None,
            priority=record.priority,
            output_path=output_path,
        )
        enqueued.append(task)

    assert len(enqueued) == 1
    assert enqueued[0].output_mode == OutputMode.SILENT
    assert enqueued[0].initiative_context == "Check disk usage"


# ---------------------------------------------------------------------------
# CronTrigger — config-defined jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_trigger_fires_config_job(tmp_path):
    """Config-defined CronJob is enqueued when due."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "cron.lock"

    job = CronJob(
        name="config-job",
        schedule="every 1s",
        context="Do the thing.",
        output_mode=OutputMode.SILENT,
    )
    trigger = CronTrigger(
        jobs=[job],
        state_path=state_path,
        lock_path=lock_path,
        tick_seconds=9999,
    )

    enqueued: list[AgentTask] = []
    now = datetime.now(UTC)
    state: dict = {}

    # Fire manually without the async loop
    if trigger._is_due(job, now, state):
        task = AgentTask(
            task_id="cfg001",
            title=job.name,
            initiative_context=job.context,
            triggered_by=f"cron:{job.name}",
            output_mode=job.output_mode,
            persona=job.persona,
            priority=job.priority,
        )
        enqueued.append(task)
        state[job.name] = now.isoformat()

    assert len(enqueued) == 1
    assert enqueued[0].triggered_by == "cron:config-job"


# ---------------------------------------------------------------------------
# make_cron_trigger
# ---------------------------------------------------------------------------


def test_make_cron_trigger(tmp_path):
    trigger, store = make_cron_trigger(
        jobs_path=tmp_path / "jobs.json",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )
    assert isinstance(trigger, CronTrigger)
    assert isinstance(store, CronJobStore)
    assert trigger._store is store


# ---------------------------------------------------------------------------
# CronCreateTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_create_basic(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)

    result = await tool.execute(
        {
            "name": "morning-brief",
            "schedule": "0 9 * * *",
            "context": "Summarise today's tasks.",
            "delivery": "local",
            "priority": 5,
        }
    )

    assert not result.is_error
    assert "morning-brief" in result.content

    jobs = store.list()
    assert len(jobs) == 1
    assert jobs[0].name == "morning-brief"
    assert jobs[0].schedule == "0 9 * * *"
    assert jobs[0].priority == 5


@pytest.mark.asyncio
async def test_cron_create_natural_language(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)

    result = await tool.execute(
        {"name": "ping", "schedule": "every 30m", "context": "Ping the server."}
    )

    assert not result.is_error
    assert "every:1800" in result.content


@pytest.mark.asyncio
async def test_cron_create_is_idempotent_for_equivalent_job(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)
    request = {"name": "ping", "schedule": "30m", "context": "Ping the server."}

    first = await tool.execute(request)
    second = await tool.execute({**request, "schedule": "every 30m"})

    assert not first.is_error
    assert not second.is_error
    assert "already exists" in second.content
    assert len(store.list()) == 1


@pytest.mark.asyncio
async def test_cron_create_requires_explicit_delete_before_replacement(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)
    await tool.execute({"name": "ping", "schedule": "30m", "context": "Ping the server."})

    result = await tool.execute({"name": "ping", "schedule": "1m", "context": "Ping the server."})

    assert result.is_error
    assert "Delete that job explicitly" in result.content
    assert len(store.list()) == 1


@pytest.mark.asyncio
async def test_cron_create_missing_name(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)
    result = await tool.execute({"schedule": "0 9 * * *", "context": "hi"})
    assert result.is_error


@pytest.mark.asyncio
async def test_cron_create_missing_schedule(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)
    result = await tool.execute({"name": "x", "context": "hi"})
    assert result.is_error


@pytest.mark.asyncio
async def test_cron_create_missing_context(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)
    result = await tool.execute({"name": "x", "schedule": "0 9 * * *"})
    assert result.is_error


@pytest.mark.asyncio
async def test_cron_create_invalid_delivery(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)
    result = await tool.execute(
        {
            "name": "x",
            "schedule": "0 9 * * *",
            "context": "hi",
            "delivery": "unknown",
        }
    )
    assert result.is_error


@pytest.mark.asyncio
async def test_cron_create_with_persona(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)
    result = await tool.execute(
        {
            "name": "scribe",
            "schedule": "30m",
            "context": "Write diary.",
            "persona": "assistant",
        }
    )
    assert not result.is_error
    jobs = store.list()
    assert jobs[0].persona == "assistant"


@pytest.mark.asyncio
async def test_cron_create_sleipnir_delivery(tmp_path):
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)
    result = await tool.execute(
        {
            "name": "broadcast",
            "schedule": "0 8 * * *",
            "context": "Morning report.",
            "delivery": "sleipnir",
        }
    )
    assert not result.is_error
    assert store.list()[0].delivery == "sleipnir"


# ---------------------------------------------------------------------------
# CronListTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_list_empty(tmp_path):
    store = _make_store(tmp_path)
    tool = CronListTool(store)
    result = await tool.execute({})
    assert not result.is_error
    assert "No cron jobs" in result.content


@pytest.mark.asyncio
async def test_cron_list_shows_jobs(tmp_path):
    store = _make_store(tmp_path)
    store.create(_make_record(job_id="aaa", name="alpha"))
    store.create(_make_record(job_id="bbb", name="beta"))

    tool = CronListTool(store)
    result = await tool.execute({})
    assert not result.is_error
    assert "alpha" in result.content
    assert "beta" in result.content
    assert "2 total" in result.content


@pytest.mark.asyncio
async def test_cron_list_enabled_only(tmp_path):
    store = _make_store(tmp_path)
    store.create(_make_record(job_id="en1", name="enabled-job", enabled=True))
    store.create(_make_record(job_id="dis1", name="disabled-job", enabled=False))

    tool = CronListTool(store)
    result = await tool.execute({"enabled_only": True})
    assert not result.is_error
    assert "enabled-job" in result.content
    assert "disabled-job" not in result.content


# ---------------------------------------------------------------------------
# CronDeleteTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_delete_existing(tmp_path):
    store = _make_store(tmp_path)
    rec = _make_record(job_id="del00001")
    store.create(rec)

    tool = CronDeleteTool(store)
    result = await tool.execute({"job_id": "del00001"})
    assert not result.is_error
    assert "del00001" in result.content
    assert store.list() == []


@pytest.mark.asyncio
async def test_cron_delete_missing(tmp_path):
    store = _make_store(tmp_path)
    tool = CronDeleteTool(store)
    result = await tool.execute({"job_id": "nope"})
    assert result.is_error


@pytest.mark.asyncio
async def test_cron_delete_missing_job_id(tmp_path):
    store = _make_store(tmp_path)
    tool = CronDeleteTool(store)
    result = await tool.execute({})
    assert result.is_error


# ---------------------------------------------------------------------------
# build_cron_tools
# ---------------------------------------------------------------------------


def test_build_cron_tools(tmp_path):
    store = _make_store(tmp_path)
    tools = build_cron_tools(store)
    names = {t.name for t in tools}
    assert names == {"cron_create", "cron_list", "cron_delete"}


def test_cron_tools_permission(tmp_path):
    store = _make_store(tmp_path)
    for tool in build_cron_tools(store):
        assert tool.required_permission == "cron:manage"


def test_cron_tools_have_valid_schema(tmp_path):
    store = _make_store(tmp_path)
    for tool in build_cron_tools(store):
        schema = tool.input_schema
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"


# ---------------------------------------------------------------------------
# AgentTask output_path
# ---------------------------------------------------------------------------


def test_agent_task_output_path_default():
    task = AgentTask(
        task_id="t1",
        title="test",
        initiative_context="do stuff",
        triggered_by="cron:abc",
        output_mode=OutputMode.SILENT,
    )
    assert task.output_path is None


def test_agent_task_output_path_set(tmp_path):
    path = tmp_path / "output.md"
    task = AgentTask(
        task_id="t2",
        title="test",
        initiative_context="do stuff",
        triggered_by="cron:abc",
        output_mode=OutputMode.SILENT,
        output_path=path,
    )
    assert task.output_path == path


# ---------------------------------------------------------------------------
# CronTrigger.run() — full async loop via mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_trigger_run_fires_and_exits(tmp_path):
    """run() fires a due job then exits on CancelledError from asyncio.sleep."""
    store = _make_store(tmp_path)
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "cron.lock"

    rec = _make_record(job_id="runtest001", name="run-test", schedule="every 1s")
    store.create(rec)

    trigger = CronTrigger(
        jobs=[],
        state_path=state_path,
        lock_path=lock_path,
        tick_seconds=0,
        store=store,
    )

    enqueued: list[AgentTask] = []

    async def fake_enqueue(task: AgentTask) -> None:
        enqueued.append(task)

    # Sleep raises CancelledError immediately → loop exits after one tick
    sleep_mock = AsyncMock(side_effect=asyncio.CancelledError)
    with patch("ravn.adapters.triggers.cron.asyncio.sleep", new=sleep_mock):
        with suppress(asyncio.CancelledError):
            await trigger.run(fake_enqueue)

    # Job was due (first time, no state) → should have fired
    assert len(enqueued) == 1
    assert enqueued[0].triggered_by == f"cron:{rec.job_id}"
    assert enqueued[0].output_path is not None

    # State was persisted
    assert state_path.exists()


@pytest.mark.asyncio
async def test_cron_trigger_run_config_job(tmp_path):
    """run() fires a config-defined job."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "cron.lock"

    job = CronJob(
        name="run-config",
        schedule="every 1s",
        context="config job context",
        output_mode=OutputMode.AMBIENT,
    )
    trigger = CronTrigger(
        jobs=[job],
        state_path=state_path,
        lock_path=lock_path,
        tick_seconds=0,
    )

    enqueued: list[AgentTask] = []

    async def fake_enqueue(task: AgentTask) -> None:
        enqueued.append(task)

    sleep_mock = AsyncMock(side_effect=asyncio.CancelledError)
    with patch("ravn.adapters.triggers.cron.asyncio.sleep", new=sleep_mock):
        with suppress(asyncio.CancelledError):
            await trigger.run(fake_enqueue)

    assert len(enqueued) == 1
    assert enqueued[0].triggered_by == "cron:run-config"
    assert enqueued[0].output_mode == OutputMode.AMBIENT


@pytest.mark.asyncio
async def test_cron_trigger_run_lock_held(tmp_path):
    """run() exits without firing when lock cannot be acquired."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "cron.lock"

    trigger = CronTrigger(jobs=[], state_path=state_path, lock_path=lock_path)

    enqueued: list[AgentTask] = []

    with patch.object(trigger, "_acquire_lock", return_value=None):
        await trigger.run(AsyncMock())  # should return immediately

    assert enqueued == []


# ---------------------------------------------------------------------------
# CronTrigger._save_state / _load_state
# ---------------------------------------------------------------------------


def test_cron_trigger_save_and_load_state(tmp_path):
    """State round-trips through JSON file."""
    trigger = CronTrigger(
        jobs=[],
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )
    state = {"job-a": "2026-04-08T09:00:00+00:00"}
    trigger._save_state(state)
    loaded = trigger._load_state()
    assert loaded == state


def test_cron_trigger_load_state_missing(tmp_path):
    trigger = CronTrigger(
        jobs=[],
        state_path=tmp_path / "missing.json",
        lock_path=tmp_path / "lock",
    )
    assert trigger._load_state() == {}


def test_cron_trigger_load_state_corrupt(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("not-json")
    trigger = CronTrigger(jobs=[], state_path=state_path, lock_path=tmp_path / "lock")
    assert trigger._load_state() == {}


# ---------------------------------------------------------------------------
# _field_matches — non-wildcard base step
# ---------------------------------------------------------------------------


def test_field_matches_base_step():
    """'2/3' means 'starting at 2, every 3': matches 2, 5, 8, 11..."""
    assert _field_matches(2, "2/3") is True
    assert _field_matches(5, "2/3") is True
    assert _field_matches(8, "2/3") is True
    assert _field_matches(3, "2/3") is False  # not in sequence
    assert _field_matches(1, "2/3") is False  # below start


# ---------------------------------------------------------------------------
# _is_due_canonical — bad ISO in once: branch
# ---------------------------------------------------------------------------


def test_is_due_canonical_bad_iso():
    trigger = CronTrigger(jobs=[])
    # "once:not-a-date" should not fire (ValueError caught)
    assert trigger._is_due_canonical("once:not-a-date", "k", datetime.now(UTC), {}) is False


# ---------------------------------------------------------------------------
# Near-duplicate and cap guards on cron_create
#
# Calibrated against the real cron store of the valhalla k8s resident, which
# accumulated 24 jobs over five days — 20 of them the same etcd-latency check
# under different names. The existing exact-match dedup never fired once: the
# resident restated itself every time. The contexts below are taken verbatim
# from that store.
# ---------------------------------------------------------------------------

_DUPLICATE_SIMILARITY = 0.5


def _guarded_tool(tmp_path: Path, *, max_jobs: int = 0) -> CronCreateTool:
    return CronCreateTool(
        _make_store(tmp_path),
        max_jobs=max_jobs,
        duplicate_similarity=_DUPLICATE_SIMILARITY,
    )


@pytest.mark.asyncio
async def test_cron_create_refuses_reworded_restatement(tmp_path):
    """The production case: same intent, different name and wording."""
    tool = _guarded_tool(tmp_path)
    await tool.execute(
        {
            "name": "etcd-latency-check",
            "schedule": "every 30m",
            "context": (
                "Check etcd pod logs for latency warnings and apiserver logs for "
                "connection errors to monitor ongoing etcd performance issues"
            ),
        }
    )

    result = await tool.execute(
        {
            "name": "etcd-apiserver-latency-check",
            "schedule": "every 15m",
            "context": (
                "Monitor etcd and kube-apiserver performance: check pod logs for etcd "
                "apply latency warnings and kube-apiserver connection errors"
            ),
        }
    )

    assert result.is_error
    assert "restates an existing one" in result.content
    assert "etcd-latency-check" in result.content
    assert len(tool._store.list()) == 1


@pytest.mark.asyncio
async def test_cron_create_refuses_restatement_by_name_alone(tmp_path):
    """Distinct wording, but the names say the same thing."""
    tool = _guarded_tool(tmp_path)
    await tool.execute(
        {
            "name": "etcd-monitoring-check",
            "schedule": "30m",
            "context": "Check etcd performance and apiserver-etcd connectivity for issues",
        }
    )

    result = await tool.execute(
        {
            "name": "etcd-monitoring-followup",
            "schedule": "every 15m",
            "context": "Re-read the snapshot restore log written by the last backup run",
        }
    )

    assert result.is_error
    assert "restates an existing one" in result.content
    assert len(tool._store.list()) == 1


@pytest.mark.asyncio
async def test_cron_create_allows_genuinely_different_jobs(tmp_path):
    """The guard must not collapse an agent's whole schedule into one job."""
    tool = _guarded_tool(tmp_path)
    jobs = [
        ("etcd-snapshot-watch", "Observe etcd snapshot events in the valhalla environment"),
        ("morning-brief", "Summarise the overnight tracker movement for the operator"),
        ("cert-expiry-sweep", "List TLS certificates expiring within fourteen days"),
        ("pvc-capacity-report", "Report persistent volume claims above ninety percent full"),
    ]
    for name, context in jobs:
        result = await tool.execute({"name": name, "schedule": "every 1h", "context": context})
        assert not result.is_error, f"{name} was wrongly refused: {result.content}"

    assert len(tool._store.list()) == len(jobs)


@pytest.mark.asyncio
async def test_cron_create_ignores_silent_marker_and_case_ids_when_comparing(tmp_path):
    """Shared bookkeeping tokens must not make unrelated jobs look alike.

    Both contexts carry a ``[SILENT]`` prefix and a case reference; stripped of
    those they have almost nothing in common and both must be allowed.
    """
    tool = _guarded_tool(tmp_path)
    first = await tool.execute(
        {
            "name": "watch-disk",
            "schedule": "15m",
            "context": "[SILENT] Resident case task_19fdf1bb210_0197: re-check node disk pressure.",
        }
    )
    second = await tool.execute(
        {
            "name": "chase-tracker",
            "schedule": "15m",
            "context": "[SILENT] Resident case task_19feb00794d_0394: chase the stalled review.",
        }
    )

    assert not first.is_error
    assert not second.is_error
    assert len(tool._store.list()) == 2


_UNRELATED_DUTIES = [
    ("cert-expiry-sweep", "List TLS certificates expiring within the next fourteen days."),
    ("pvc-capacity-report", "Report persistent volume claims above ninety percent full."),
    ("morning-brief", "Summarise overnight tracker movement for the operator."),
]


@pytest.mark.asyncio
async def test_cron_create_enforces_job_cap(tmp_path):
    tool = _guarded_tool(tmp_path, max_jobs=3)
    for name, context in _UNRELATED_DUTIES:
        result = await tool.execute({"name": name, "schedule": "every 1h", "context": context})
        assert not result.is_error, f"{name} was wrongly refused: {result.content}"

    overflow = await tool.execute(
        {
            "name": "image-pull-audit",
            "schedule": "every 1h",
            "context": "Audit container image pull failures raised by the registry mirror.",
        }
    )

    assert overflow.is_error
    assert "Cron job limit reached: 3 of 3" in overflow.content
    assert "cert-expiry-sweep" in overflow.content
    assert len(tool._store.list()) == 3


@pytest.mark.asyncio
async def test_cron_create_cap_ignores_disabled_jobs(tmp_path):
    """A disabled job costs nothing to run, so it must not consume the budget."""
    store = _make_store(tmp_path)
    store.create(
        _make_record(
            job_id="deadbeefdeadbeef",
            name="retired-probe",
            context="Inspect the retired subsystem for stale lock files each hour.",
            enabled=False,
        )
    )
    tool = CronCreateTool(store, max_jobs=1, duplicate_similarity=_DUPLICATE_SIMILARITY)

    result = await tool.execute(
        {
            "name": "cert-expiry-sweep",
            "schedule": "every 1h",
            "context": "List TLS certificates expiring within the next fourteen days.",
        }
    )

    assert not result.is_error
    assert len(store.list()) == 2


@pytest.mark.asyncio
async def test_cron_create_guards_are_off_by_default(tmp_path):
    """Defaults must not change behaviour for callers that do not opt in."""
    store = _make_store(tmp_path)
    tool = CronCreateTool(store)

    await tool.execute({"name": "a-check", "schedule": "30m", "context": "Check etcd latency now."})
    result = await tool.execute(
        {"name": "b-check", "schedule": "30m", "context": "Check the etcd latency again now."}
    )

    assert not result.is_error
    assert len(store.list()) == 2
