"""Kubernetes-event residents judge coalesced windows, not one turn per event.

Measured 2026-10-28: Sigrun queued 920 tasks, Eir 1055 and Gondul 959. Over
six hours Sigrun enqueued 295 and finished 32, and 283 of the 295 were single
``Pod <ns>/<pod>: warning|critical`` events, the same pod recurring up to 29
times. Regin and Ivaldi, whose signals go through the durable inbox, sat at 0.

With the inbox enabled for Environment signals it is the only producer: repeats
fold into one slot while the resident has not looked, a home turn judges the
slot with its counts and ranges, and a critical observation wakes that turn
instead of waiting for the routine interval.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.cli.commands import _build_environment_signal_runtime
from ravn.config import EnvironmentConfig, InitiativeConfig, Settings, SignalSourceConfig
from ravn.domain.models import (
    RESIDENT_HOME_PRIORITY,
    SIGNAL_BACKLOG_TRIGGER,
    AgentTask,
    OutputMode,
)
from ravn.drive_loop import DriveLoop
from ravn.environment_signal_runtime import EnvironmentSignalRuntime
from ravn.resident_inbox import LocalResidentInbox
from ravn.resident_runtime import ResidentHomeTrigger, ResidentRuntime
from ravn.signal_backlog import fold_signal_backlog, is_per_signal_task
from sleipnir.adapters.in_process import InProcessBus

POD = "api-7d9"


def _k8s_event(index: int, *, reason: str = "Unhealthy", pod: str = POD) -> dict:
    observed = datetime(2026, 10, 28, 9, 0, tzinfo=UTC) + timedelta(seconds=index)
    return {
        "metadata": {"name": f"{pod}.{index}", "uid": f"uid-{pod}-{index}", "namespace": "shop"},
        "involvedObject": {"kind": "Pod", "name": pod, "namespace": "shop", "apiVersion": "v1"},
        "reason": reason,
        "message": f"Readiness probe failed ({index})",
        "type": "Warning",
        "count": 1,
        "lastTimestamp": observed.isoformat(),
    }


def _settings(tmp_path: Path, events_file: Path) -> Settings:
    settings = Settings(
        environment=EnvironmentConfig(
            id="ymir",
            name="Ymir",
            type="k8s",
            resident_name="Sigrun",
            signal_sources=[
                SignalSourceConfig(
                    id="kubernetes-events",
                    name="Kubernetes Events",
                    kind="kubernetes",
                    adapter="ravn.adapters.environment_signals.KubernetesSignalAdapter",
                    kwargs={
                        "raw_items_file": str(events_file),
                        "critical_reasons": ["backoff"],
                    },
                )
            ],
        )
    )
    settings.state_dir = str(tmp_path / "state")
    settings.resident_inbox.environment_signals_enabled = True
    return settings


class _QueueProbe:
    """The drive loop surface the signal runtime enqueues through."""

    def __init__(self) -> None:
        self.tasks: list[AgentTask] = []

    async def enqueue(self, task: AgentTask) -> bool:
        self.tasks.append(task)
        return True


async def _resident(tmp_path: Path, events: list[dict]):
    events_file = tmp_path / "events.json"
    events_file.write_text(json.dumps(events))
    inbox = LocalResidentInbox(tmp_path / "inbox")
    runtime = ResidentRuntime(state=LocalResidentState(tmp_path / "resident"), inbox=inbox)
    queue = _QueueProbe()
    signals = _build_environment_signal_runtime(
        _settings(tmp_path, events_file),
        drive_loop=queue,
        publisher=InProcessBus(),
        resident_inbox=inbox,
        resident_runtime=runtime,
        owns_publisher=False,
    )
    assert signals is not None
    return signals, inbox, runtime, queue, events_file


async def _home_task(runtime: ResidentRuntime) -> AgentTask | None:
    return await runtime.next_home_task(limit=50, persona=None, output_mode=OutputMode.AMBIENT)


@pytest.mark.asyncio
async def test_repeated_warnings_about_one_pod_become_one_slot_and_one_turn(tmp_path) -> None:
    signals, inbox, runtime, queue, _file = await _resident(
        tmp_path, [_k8s_event(index) for index in range(12)]
    )

    assert await signals.collect_once() == 12

    # Nothing is queued per event: the inbox is the only producer.
    assert queue.tasks == []
    pending = await inbox.list_signals(limit=50)
    assert len(pending) == 1
    assert pending[0][1].observation_count == 12

    task = await _home_task(runtime)
    assert task is not None
    assert task.priority == RESIDENT_HOME_PRIORITY
    # The turn's receipt says how many observations the slot stands for, over
    # what span, and which values varied — nothing is folded away silently.
    assert "Coalesced: 12 observations of this exact shape" in task.initiative_context
    assert POD in task.initiative_context
    assert "Raw archive range:" in task.initiative_context
    # While that turn is pending, no second one is produced.
    assert await _home_task(runtime) is None


@pytest.mark.asyncio
async def test_warnings_arriving_while_a_turn_is_pending_join_the_slot_not_the_queue(
    tmp_path,
) -> None:
    signals, inbox, runtime, queue, events_file = await _resident(
        tmp_path, [_k8s_event(index) for index in range(3)]
    )
    await signals.collect_once()
    task = await _home_task(runtime)
    assert task is not None

    events_file.write_text(json.dumps([_k8s_event(index) for index in range(3, 10)]))
    assert await signals.collect_once() == 7

    assert queue.tasks == []
    assert await _home_task(runtime) is None
    # The pending turn acknowledges exactly what it saw; the seven that arrived
    # meanwhile stay pending for the next turn.
    await inbox.acknowledge(tuple(task.resident_inbox_refs), expected=task.resident_inbox_expected)
    pending = await inbox.list_signals(limit=50)
    assert [signal.observation_count for _ref, signal in pending] == [7]


@pytest.mark.asyncio
async def test_a_critical_wakes_the_home_turn_ahead_of_queued_work(tmp_path) -> None:
    signals, _inbox, runtime, _queue, _file = await _resident(
        tmp_path, [_k8s_event(0, reason="BackOff")]
    )
    loop = DriveLoop(
        agent_factory=lambda **_kwargs: None,
        config=InitiativeConfig(enabled=True, queue_journal_path=str(tmp_path / "queue.json")),
        settings=Settings(),
    )
    for index in range(20):
        await loop.enqueue(
            AgentTask(
                task_id=f"task-backlog-{index}",
                title="Pod shop/other: warning",
                initiative_context="judge it",
                triggered_by="signal:signal.kubernetes.event",
                output_mode=OutputMode.AMBIENT,
                priority=3,
            )
        )
    trigger = ResidentHomeTrigger(
        runtime,
        interval_seconds=3600,
        max_signals=50,
        persona=None,
        output_mode=OutputMode.AMBIENT,
    )

    await signals.collect_once()

    # The routine interval is an hour; the critical cuts it short.
    await asyncio.wait_for(runtime.wait_for_home_wake(3600), timeout=1)
    assert await trigger.run_once(loop.enqueue) is True
    home_id = loop.queued_task_ids()[0]
    home = next(task for _p, _c, task in loop._queue._queue if task.task_id == home_id)
    assert home.triggered_by == "resident:home"
    assert "BackOff" in home.initiative_context


@pytest.mark.asyncio
async def test_a_critical_during_a_home_turn_wakes_the_next_one_when_it_ends(tmp_path) -> None:
    signals, _inbox, runtime, _queue, events_file = await _resident(tmp_path, [_k8s_event(0)])
    await signals.collect_once()
    task = await _home_task(runtime)
    assert task is not None

    events_file.write_text(json.dumps([_k8s_event(1, reason="BackOff")]))
    await signals.collect_once()
    # The request lands while a home turn is pending, so this pass finds
    # nothing to do and the wake waits for that turn to end...
    await runtime.wait_for_home_wake(3600)
    assert await _home_task(runtime) is None
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runtime.wait_for_home_wake(3600), timeout=0.05)

    # ...and fires once it does, instead of an interval later.
    runtime.release_failed_task(task)
    await asyncio.wait_for(runtime.wait_for_home_wake(3600), timeout=1)
    assert await _home_task(runtime) is not None


@pytest.mark.asyncio
async def test_home_trigger_loop_judges_a_critical_without_waiting_the_interval(
    tmp_path,
) -> None:
    signals, _inbox, runtime, _queue, events_file = await _resident(tmp_path, [_k8s_event(0)])
    await signals.collect_once()
    trigger = ResidentHomeTrigger(
        runtime,
        interval_seconds=0.1,
        max_signals=50,
        persona=None,
        output_mode=OutputMode.AMBIENT,
    )
    queued: list[AgentTask] = []
    first_turn = asyncio.Event()

    async def enqueue(task: AgentTask) -> bool:
        queued.append(task)
        # From here the routine interval is far away; only a critical brings
        # the next turn.
        trigger._interval = 3600
        first_turn.set()
        return True

    loop_task = asyncio.create_task(trigger.run(enqueue))
    try:
        await asyncio.wait_for(first_turn.wait(), timeout=1)
        runtime.release_failed_task(queued[0])
        events_file.write_text(json.dumps([_k8s_event(1, reason="BackOff")]))
        await signals.collect_once()
        for _ in range(100):
            if len(queued) == 2:
                break
            await asyncio.sleep(0.01)
    finally:
        loop_task.cancel()
    assert len(queued) == 2
    assert "BackOff" in queued[1].initiative_context


@pytest.mark.asyncio
async def test_warnings_do_not_cut_the_home_interval_short(tmp_path) -> None:
    signals, _inbox, runtime, _queue, _file = await _resident(tmp_path, [_k8s_event(0)])

    await signals.collect_once()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runtime.wait_for_home_wake(3600), timeout=0.05)


def test_inbox_intake_with_a_queue_requires_the_home_wake(tmp_path) -> None:
    settings = _settings(tmp_path, tmp_path / "events.json")

    with pytest.raises(ValueError, match="request_home_wake"):
        EnvironmentSignalRuntime(
            settings=settings,
            publisher=InProcessBus(),
            enqueue=_QueueProbe().enqueue,
            durable_home_enabled=True,
        )


# ---------------------------------------------------------------------------
# A restored per-event backlog folds into one window
# ---------------------------------------------------------------------------


def _signal_task(index: int, *, pod: str, priority: int = 8, severity: str = "warning"):
    return AgentTask(
        task_id=f"task-{pod}-{index}",
        title=f"Pod shop/{pod}: {severity}",
        initiative_context=f"# Environment signal — observation {index}",
        triggered_by="signal:signal.kubernetes.event",
        output_mode=OutputMode.AMBIENT,
        persona="k8s-valkyrie",
        priority=priority,
        workflow_parent_event_id=f"evt-{pod}-{index}",
        created_at=datetime(2026, 10, 28, 6, 0, tzinfo=UTC) + timedelta(minutes=index),
    )


def test_fold_groups_by_object_and_keeps_the_most_urgent_priority() -> None:
    tasks = [
        *[_signal_task(index, pod="niuu-ting") for index in range(29)],
        *[_signal_task(index, pod="room-skuld") for index in range(19)],
        _signal_task(0, pod="db-0", priority=3, severity="critical"),
    ]

    window = fold_signal_backlog(
        tasks, record_ref="/state/queue.folded.json", sample_groups=2, max_signal_refs=3
    )

    assert window.triggered_by == SIGNAL_BACKLOG_TRIGGER
    assert window.priority == 3
    assert window.persona == "k8s-valkyrie"
    assert not is_per_signal_task(window)
    context = window.initiative_context
    assert "49 observation(s) across 3 object(s)" in context
    assert "`Pod shop/niuu-ting: warning` — 29 task(s)" in context
    assert "`Pod shop/room-skuld: warning` — 19 task(s)" in context
    assert "…and 1 more object(s) covering 1 task(s), not shown" in context
    assert "/state/queue.folded.json" in context
    assert "evt-niuu-ting-2" in context
    assert "evt-niuu-ting-3" not in context


def test_fold_refuses_an_empty_backlog() -> None:
    with pytest.raises(ValueError):
        fold_signal_backlog([], record_ref="x", sample_groups=1, max_signal_refs=1)


def _journal_loop(tmp_path: Path) -> DriveLoop:
    return DriveLoop(
        agent_factory=lambda **_kwargs: None,
        config=InitiativeConfig(enabled=True, queue_journal_path=str(tmp_path / "queue.json")),
        settings=Settings(),
    )


def _write_journal(tmp_path: Path, queued: list[AgentTask], inflight: list[AgentTask]) -> None:
    (tmp_path / "queue.json").write_text(
        json.dumps(
            {
                "queue": [DriveLoop._task_journal_record(task) for task in queued],
                "inflight": [DriveLoop._task_journal_record(task) for task in inflight],
            }
        )
    )


def test_restart_folds_the_per_event_backlog_and_records_every_task(tmp_path) -> None:
    backlog = [
        *[_signal_task(index, pod="niuu-ting") for index in range(29)],
        _signal_task(0, pod="db-0", priority=3, severity="critical"),
    ]
    running = _signal_task(99, pod="running")
    operator = AgentTask(
        task_id="task-operator",
        title="Directed message from user",
        initiative_context="hello",
        triggered_by="skuld:directed_message",
        output_mode=OutputMode.SURFACE,
        priority=0,
    )
    home = AgentTask(
        task_id="task-home",
        title="Resident home turn (1 observations)",
        initiative_context="home",
        triggered_by="resident:home",
        output_mode=OutputMode.AMBIENT,
        priority=RESIDENT_HOME_PRIORITY,
        resident_case_id="resident-home-abc",
        resident_inbox_refs=["pending/abc.md"],
        resident_inbox_expected={"pending/abc.md": "raw/2026-10-28.ndjson#7"},
    )
    _write_journal(tmp_path, [*backlog, operator, home], [running])
    loop = _journal_loop(tmp_path)

    loop._load_journal()

    queued = sorted(
        (task for _priority, _counter, task in loop._queue._queue), key=lambda t: t.priority
    )
    assert [task.task_id for task in queued[:2]] == ["task-operator", "task-home"]
    windows = [task for task in queued if task.triggered_by == SIGNAL_BACKLOG_TRIGGER]
    assert len(windows) == 1
    assert windows[0].priority == 3
    assert "30 observation(s) across 2 object(s)" in windows[0].initiative_context
    # The task that was running at the restart resumes as itself.
    assert "task-running-99" in {task.task_id for task in queued}
    assert loop._restored_inflight_task_ids == {"task-running-99"}
    restored_home = next(task for task in queued if task.task_id == "task-home")
    assert restored_home.resident_inbox_expected == {"pending/abc.md": "raw/2026-10-28.ndjson#7"}

    records = list(tmp_path.glob("queue.folded-*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["window_task_id"] == windows[0].task_id
    assert sorted(entry["task_id"] for entry in record["tasks"]) == sorted(
        task.task_id for task in backlog
    )
    assert str(records[0]) in windows[0].initiative_context
    # The journal now holds the window, not the folded tasks.
    journal = json.loads((tmp_path / "queue.json").read_text())
    journal_ids = {entry["task_id"] for entry in journal["queue"] + journal["inflight"]}
    assert windows[0].task_id in journal_ids
    assert not journal_ids & {task.task_id for task in backlog}


def test_a_single_restored_signal_task_is_left_as_it_is(tmp_path) -> None:
    only = _signal_task(0, pod="niuu-ting")
    _write_journal(tmp_path, [only], [])
    loop = _journal_loop(tmp_path)

    loop._load_journal()

    assert loop.queued_task_ids() == [only.task_id]
    assert not list(tmp_path.glob("queue.folded-*.json"))


def test_a_restored_signal_task_no_longer_claims_a_shared_inbox_slot(tmp_path) -> None:
    running = _signal_task(0, pod="db-0", priority=3, severity="critical")
    running.resident_inbox_refs = ["pending/k8s-shape.md"]
    _write_journal(tmp_path, [], [running])
    loop = _journal_loop(tmp_path)

    loop._load_journal()

    restored = next(task for _p, _c, task in loop._queue._queue)
    assert restored.task_id == running.task_id
    assert restored.resident_inbox_refs == []


def test_restart_refuses_to_fold_without_a_durable_record(tmp_path, monkeypatch) -> None:
    backlog = [_signal_task(index, pod="niuu-ting") for index in range(3)]
    _write_journal(tmp_path, backlog, [])
    before = (tmp_path / "queue.json").read_text()
    loop = _journal_loop(tmp_path)
    original_write = Path.write_text

    def failing_write(self: Path, *args, **kwargs):
        if ".folded-" in self.name:
            raise OSError("read-only file system")
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)

    with pytest.raises(RuntimeError, match="Nothing was folded"):
        loop._load_journal()
    assert (tmp_path / "queue.json").read_text() == before
