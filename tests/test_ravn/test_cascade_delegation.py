"""Tests for NIU-457 — cascade delegation: tasks, workers, teams, epistemic sovereignty.

Coverage targets:
- Task ID generation: format, uniqueness, timestamp ordering
- AgentTask model: session_id derivation, deadline, priority, output_mode
- DriveLoop lifecycle: enqueue → running → complete/failed/cancelled
- DriveLoop deadline: task discarded before and after enqueue
- DriveLoop queue full: task discarded when at capacity
- DriveLoop event publishing: TASK_STARTED and TASK_COMPLETE events
- DriveLoop surface escalation: [SURFACE] response re-delivery
- DriveLoop heartbeat: periodic event publication
- DriveLoop journal: persist and restore pending tasks
- Budget sharing: parent + sub-ravn share IterationBudget object
- Budget cascade: task_ceiling limits sub-agent without cutting global pool
- Budget exhaustion: warning propagates; sub-ravn stops when ceiling hit
- Permission scoping: SpawnConfig carries permission_mode to spawned peer
- Epistemic sovereignty: task session_ids are independent, SharedContext is explicit
- Team simulation (Mode 1): N tasks → all execute concurrently → parent collects all
- Error handling: sub-task exception → status unknown → parent continues
- E2E delegation: mock LLM calls task_create → task runs → parent calls task_collect
- E2E parallel team: parent creates 3 tasks → all complete → combined output
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.tools.cascade_tools import (
    TaskCollectTool,
    TaskCreateTool,
    TaskListTool,
    TaskStatusTool,
    TaskStopTool,
    _new_task_id,
    build_cascade_tools,
)
from ravn.budget import IterationBudget
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import AgentTask, OutputMode, SharedContext, ToolResult
from ravn.ports.spawn import SpawnConfig
from tests.test_ravn.conftest import (
    _FakeDiscovery,
    _FakePeer,
    _FakeSpawnAdapter,
    _make_agent_task,
    _make_drive_loop,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TASK_ID_PATTERN = re.compile(r"^task_\d{14}_[0-9a-f]{6}$")


class _RecordingPublisher:
    """Collects all published events for inspection."""

    def __init__(self) -> None:
        self.events: list[RavnEvent] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)  # type: ignore[arg-type]

    def of_type(self, event_type: RavnEventType) -> list[RavnEvent]:
        return [e for e in self.events if e.type == event_type]


# ---------------------------------------------------------------------------
# Task ID generation
# ---------------------------------------------------------------------------


class TestTaskIdGeneration:
    def test_format_matches_pattern(self):
        """task_YYYYMMDDHHMMSS_xxxxxx pattern."""
        tid = _new_task_id()
        assert _TASK_ID_PATTERN.match(tid), f"unexpected format: {tid!r}"

    def test_uniqueness(self):
        """Each call returns a distinct ID."""
        ids = {_new_task_id() for _ in range(50)}
        assert len(ids) == 50

    def test_timestamp_prefix_ordering(self):
        """Two IDs generated 1 second apart sort lexicographically by creation time."""
        import uuid

        times = [
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        ]
        call = [0]

        def _fake_now(tz=None):  # noqa: ANN001
            t = times[min(call[0], 1)]
            call[0] += 1
            return t

        uid1 = uuid.UUID("00000000-0000-0000-0000-000000000001")
        uid2 = uuid.UUID("00000000-0000-0000-0000-000000000002")
        with patch("ravn.adapters.tools.cascade_tools.datetime") as mock_dt:
            mock_dt.now.side_effect = _fake_now
            with patch("uuid.uuid4", return_value=uid1):
                id_early = _new_task_id()
            with patch("uuid.uuid4", return_value=uid2):
                id_late = _new_task_id()

        assert id_early < id_late, f"expected {id_early!r} < {id_late!r}"

    def test_hex_suffix_is_six_chars(self):
        tid = _new_task_id()
        suffix = tid.split("_")[-1]
        assert len(suffix) == 6
        assert all(c in "0123456789abcdef" for c in suffix)


# ---------------------------------------------------------------------------
# AgentTask model
# ---------------------------------------------------------------------------


class TestAgentTaskModel:
    def test_session_id_derived_from_task_id(self):
        task = _make_agent_task("task_20260407_abc123")
        assert task.session_id == "daemon_task_20260407_abc123"

    def test_session_ids_are_unique_per_task(self):
        tasks = [_make_agent_task(f"task-{i}") for i in range(10)]
        session_ids = {t.session_id for t in tasks}
        assert len(session_ids) == 10

    def test_default_priority(self):
        task = AgentTask(
            task_id="t1",
            title="x",
            initiative_context="y",
            triggered_by="test",
            output_mode=OutputMode.SILENT,
        )
        assert task.priority == 10

    def test_output_modes_accepted(self):
        for mode in OutputMode:
            task = _make_agent_task(output_mode=mode)
            assert task.output_mode == mode

    def test_created_at_is_set_automatically(self):
        before = datetime.now(UTC)
        task = _make_agent_task()
        after = datetime.now(UTC)
        assert before <= task.created_at <= after

    def test_deadline_none_by_default(self):
        task = _make_agent_task()
        assert task.deadline is None

    def test_session_id_uses_daemon_prefix(self):
        task = _make_agent_task("task_abc")
        assert task.session_id.startswith("daemon_")


# ---------------------------------------------------------------------------
# DriveLoop: deadline and queue capacity
# ---------------------------------------------------------------------------


class TestDriveLoopDeadline:
    @pytest.mark.asyncio
    async def test_enqueue_discards_past_deadline(self):
        """Task with deadline in the past is silently discarded on enqueue."""
        dl = _make_drive_loop()
        past = datetime.now(UTC) - timedelta(seconds=10)
        task = _make_agent_task("expired-task", deadline=past)
        await dl.enqueue(task)
        assert dl.task_status("expired-task") == "unknown"

    @pytest.mark.asyncio
    async def test_enqueue_accepts_future_deadline(self):
        """Task with a future deadline is accepted into the queue."""
        dl = _make_drive_loop()
        future = datetime.now(UTC) + timedelta(hours=1)
        task = _make_agent_task("future-task", deadline=future)
        await dl.enqueue(task)
        assert dl.task_status("future-task") == "queued"

    @pytest.mark.asyncio
    async def test_executor_discards_expired_task(self):
        """Task whose deadline expires while it is queued is discarded by the executor."""
        executed: list[str] = []
        barrier = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                executed.append(task_id)
                barrier.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory)

        # Task with an already-expired deadline — bypass enqueue deadline check
        past = datetime.now(UTC) - timedelta(seconds=1)
        task = AgentTask(
            task_id="expired-in-queue",
            title="expires",
            initiative_context="prompt",
            triggered_by="test",
            output_mode=OutputMode.SILENT,
            deadline=past,
        )
        dl._queue.put_nowait((10, 0, task))

        loop_task = asyncio.create_task(dl.run())
        # Give the executor time to process the expired task
        await asyncio.sleep(0.15)
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)

        assert "expired-in-queue" not in executed


class TestDriveLoopQueueFull:
    @pytest.mark.asyncio
    async def test_task_discarded_when_queue_full(self):
        """When the queue is at capacity, additional tasks are silently discarded."""
        dl = _make_drive_loop(queue_max=2)
        for i in range(2):
            await dl.enqueue(_make_agent_task(f"task-{i}"))
        extra = _make_agent_task("task-extra")
        await dl.enqueue(extra)
        assert dl.task_status("task-extra") == "unknown"
        assert dl._queue.qsize() == 2


# ---------------------------------------------------------------------------
# DriveLoop: cancel
# ---------------------------------------------------------------------------


class TestDriveLoopCancel:
    @pytest.mark.asyncio
    async def test_cancel_non_running_is_noop(self):
        """Cancelling a task that is not running does not raise."""
        dl = _make_drive_loop()
        await dl.cancel("never-existed")

    @pytest.mark.asyncio
    async def test_cancel_running_task_calls_cancel(self):
        """Cancelling a running task invokes cancel() on the underlying asyncio.Task."""
        dl = _make_drive_loop()
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        dl._active_tasks["running-task"] = mock_task
        await dl.cancel("running-task")
        mock_task.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# DriveLoop: event publishing
# ---------------------------------------------------------------------------


class TestDriveLoopEventPublishing:
    @pytest.mark.asyncio
    async def test_task_started_event_published(self):
        """DriveLoop publishes TASK_STARTED when a task begins."""
        publisher = _RecordingPublisher()
        finished = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                finished.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory, event_publisher=publisher)
        await dl.enqueue(_make_agent_task("pub-task"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(finished.wait(), timeout=3.0)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        assert publisher.of_type(RavnEventType.TASK_STARTED)

    @pytest.mark.asyncio
    async def test_task_complete_event_published_on_success(self):
        """DriveLoop publishes TASK_COMPLETE after a successful task."""
        publisher = _RecordingPublisher()
        finished = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                finished.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory, event_publisher=publisher)
        await dl.enqueue(_make_agent_task("complete-task"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(finished.wait(), timeout=3.0)
            await asyncio.sleep(0.05)  # let the done-callback flush
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        assert publisher.of_type(RavnEventType.TASK_COMPLETE)

    @pytest.mark.asyncio
    async def test_task_complete_success_payload(self):
        """TASK_COMPLETE payload carries success=True when task finishes normally."""
        publisher = _RecordingPublisher()
        finished = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                finished.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory, event_publisher=publisher)
        await dl.enqueue(_make_agent_task("success-task"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(finished.wait(), timeout=3.0)
            await asyncio.sleep(0.05)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        complete = publisher.of_type(RavnEventType.TASK_COMPLETE)
        assert complete
        assert complete[0].payload["success"] is True

    @pytest.mark.asyncio
    async def test_task_complete_failure_payload(self):
        """TASK_COMPLETE payload carries success=False when agent raises."""
        publisher = _RecordingPublisher()
        finished = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                finished.set()
                raise RuntimeError("agent exploded")

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory, event_publisher=publisher)
        await dl.enqueue(_make_agent_task("fail-task"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(finished.wait(), timeout=3.0)
            await asyncio.sleep(0.05)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        complete = publisher.of_type(RavnEventType.TASK_COMPLETE)
        assert complete
        assert complete[0].payload["success"] is False


# ---------------------------------------------------------------------------
# DriveLoop: task lifecycle (queued → running → unknown)
# ---------------------------------------------------------------------------


class TestDriveLoopLifecycle:
    @pytest.mark.asyncio
    async def test_status_transitions_queued_running_unknown(self):
        """Task transitions: queued → running → unknown (after completion)."""
        started = asyncio.Event()
        done = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                started.set()
                await done.wait()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory)
        task = _make_agent_task("lifecycle-task")
        await dl.enqueue(task)
        assert dl.task_status("lifecycle-task") == "queued"

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(started.wait(), timeout=3.0)
            assert dl.task_status("lifecycle-task") == "running"
            done.set()
            await asyncio.sleep(0.1)
            assert dl.task_status("lifecycle-task") == "unknown"
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_failed_task_removed_from_active(self):
        """A task that raises an exception is removed from _active_tasks."""
        finished = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                finished.set()
                raise ValueError("intentional failure")

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory)
        await dl.enqueue(_make_agent_task("error-task"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(finished.wait(), timeout=3.0)
            await asyncio.sleep(0.05)
            assert dl.task_status("error-task") == "unknown"
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_cancelled_task_publishes_complete_false(self):
        """Cancelling a mid-run task emits TASK_COMPLETE with success=False."""
        publisher = _RecordingPublisher()
        started = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                started.set()
                await asyncio.sleep(100)

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory, event_publisher=publisher)
        await dl.enqueue(_make_agent_task("cancel-event-task"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(started.wait(), timeout=3.0)
            await dl.cancel("cancel-event-task")
            await asyncio.sleep(0.2)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        complete = publisher.of_type(RavnEventType.TASK_COMPLETE)
        assert complete
        assert complete[0].payload["success"] is False


# ---------------------------------------------------------------------------
# DriveLoop: surface escalation
# ---------------------------------------------------------------------------


class TestDriveLoopSurfaceEscalation:
    @pytest.mark.asyncio
    async def test_surface_triggered_re_delivers_response(self):
        """[SURFACE] response in RESPONSE event triggers RESPONSE re-delivery."""
        publisher = _RecordingPublisher()
        finished = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                # Emit a [SURFACE]-prefixed RESPONSE event through the channel
                await channel.emit(
                    RavnEvent(
                        type=RavnEventType.RESPONSE,
                        source="test",
                        payload={"text": "[SURFACE] Final answer!"},
                        timestamp=datetime.now(UTC),
                        urgency=0.5,
                        correlation_id=task_id or "test",
                        session_id=f"daemon_{task_id}",
                        task_id=task_id,
                    )
                )
                finished.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory, event_publisher=publisher)
        await dl.enqueue(_make_agent_task("surface-task"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(finished.wait(), timeout=3.0)
            await asyncio.sleep(0.1)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        response_events = publisher.of_type(RavnEventType.RESPONSE)
        surface_events = [e for e in response_events if e.payload.get("surface_escalation")]
        assert surface_events, "Expected a surface escalation RESPONSE event"


# ---------------------------------------------------------------------------
# DriveLoop: heartbeat
# ---------------------------------------------------------------------------


class TestDriveLoopHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_publishes_event(self):
        """Heartbeat publishes events with heartbeat=True in the payload."""
        publisher = _RecordingPublisher()
        dl = _make_drive_loop(heartbeat_seconds=1, event_publisher=publisher)

        loop_task = asyncio.create_task(dl.run())
        await asyncio.sleep(1.1)  # wait for at least one heartbeat tick
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)

        heartbeat_events = [
            e for e in publisher.events if getattr(e, "payload", {}).get("heartbeat")
        ]
        assert heartbeat_events, "Expected at least one heartbeat event"


# ---------------------------------------------------------------------------
# DriveLoop: queue journal persistence
# ---------------------------------------------------------------------------


class TestDriveLoopJournal:
    @pytest.mark.asyncio
    async def test_persist_and_restore(self, tmp_path: Path):
        """Tasks in the queue are written to the journal and restored on the next start."""
        journal = str(tmp_path / "queue.json")
        dl = _make_drive_loop(journal_path=journal)
        task = _make_agent_task("journal-task")
        task.trace_context = {
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
        }
        dl._queue.put_nowait((10, 0, task))
        dl._persist_queue()

        # New instance reads the same journal
        dl2 = _make_drive_loop(journal_path=journal)
        dl2._load_journal()
        assert dl2.task_status("journal-task") == "queued"
        restored = dl2._queue._queue[0][2]
        assert restored.trace_context == task.trace_context

    @pytest.mark.asyncio
    async def test_restored_inflight_task_recovers_durable_tool_work(self, tmp_path: Path):
        journal = str(tmp_path / "queue.json")
        first = _make_drive_loop(journal_path=journal)
        task = _make_agent_task("interrupted-task")
        first._inflight_tasks[task.task_id] = task
        first._persist_queue()

        recovered_tool = MagicMock()
        recovered_tool.name = "build_tool"
        recovered_tool.recover_pending = AsyncMock(
            return_value=[
                ToolResult(
                    tool_call_id="",
                    content="continued task-remote-1",
                )
            ]
        )
        agent = MagicMock()
        agent.tools = [recovered_tool]
        restarted = _make_drive_loop(journal_path=journal)
        restarted._settings.tools.max_result_chars = 0
        restarted._load_journal()

        results = await restarted._recover_interrupted_tool_operations(agent, task)
        repeated = await restarted._recover_interrupted_tool_operations(agent, task)

        assert results == ["### build_tool (completed)\n\ncontinued task-remote-1"]
        assert repeated == []
        recovered_tool.recover_pending.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_journal_skips_expired_tasks(self, tmp_path: Path):
        """Tasks with past deadlines are discarded during journal restore."""
        journal_path = tmp_path / "queue.json"
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        records = [
            {
                "task_id": "expired-journal-task",
                "title": "x",
                "initiative_context": "y",
                "triggered_by": "test",
                "output_mode": "silent",
                "persona": None,
                "priority": 10,
                "max_tokens": None,
                "deadline": past,
                "created_at": datetime.now(UTC).isoformat(),
            }
        ]
        journal_path.write_text(json.dumps(records))
        dl = _make_drive_loop(journal_path=str(journal_path))
        dl._load_journal()
        assert dl.task_status("expired-journal-task") == "unknown"

    @pytest.mark.asyncio
    async def test_journal_handles_corrupt_file(self, tmp_path: Path):
        """Corrupt journal does not raise — it is silently skipped."""
        journal_path = tmp_path / "queue.json"
        journal_path.write_text("{ not valid json }{{{")
        dl = _make_drive_loop(journal_path=str(journal_path))
        dl._load_journal()  # must not raise

    @pytest.mark.asyncio
    async def test_journal_handles_invalid_record(self, tmp_path: Path):
        """Journal records with missing fields are skipped gracefully."""
        journal_path = tmp_path / "queue.json"
        journal_path.write_text(json.dumps([{"task_id": "broken"}]))
        dl = _make_drive_loop(journal_path=str(journal_path))
        dl._load_journal()
        assert dl.task_status("broken") == "unknown"


# ---------------------------------------------------------------------------
# Budget sharing (cascade)
# ---------------------------------------------------------------------------


class TestBudgetCascadeSharing:
    def test_shared_budget_reflects_combined_consumption(self):
        """Parent and sub-ravn sharing the same object see combined usage."""
        shared = IterationBudget(total=100)
        shared.consume(20)  # parent
        shared.consume(15)  # sub-ravn (same object)
        assert shared.consumed == 35
        assert shared.remaining == 65

    def test_task_ceiling_limits_sub_ravn_without_cutting_global(self):
        """Sub-ravn task_ceiling exhausts locally while global pool remains."""
        parent_budget = IterationBudget(total=100)
        sub_budget = IterationBudget(
            total=parent_budget.total,
            consumed=parent_budget.consumed,
            task_ceiling=10,
        )
        sub_budget.consume(10)
        assert sub_budget.exhausted
        assert sub_budget.remaining == 0
        # The sub's total is still 100 — global pool is unaffected
        assert sub_budget.total == 100

    def test_budget_near_limit_when_ceiling_80_pct_consumed(self):
        """Sub-ravn issues a warning when its task_ceiling is 80% consumed."""
        sub_budget = IterationBudget(total=100, task_ceiling=10, near_limit_threshold=0.8)
        sub_budget.consume(8)
        suffix = sub_budget.warning_suffix()
        assert suffix is not None
        assert "Budget warning" in suffix

    def test_parent_sees_sub_ravn_consumption(self):
        """Parent remaining decreases as sub-ravn consumes from the shared object."""
        parent = IterationBudget(total=50)
        parent.consume(30)  # simulates sub-ravn work
        assert parent.remaining == 20
        assert not parent.exhausted

    def test_sub_ravn_exhaustion_emits_warning(self):
        """Sub-ravn with exhausted task_ceiling emits exhaustion warning."""
        sub_budget = IterationBudget(total=100, task_ceiling=5)
        for _ in range(5):
            sub_budget.consume()
        assert sub_budget.exhausted
        suffix = sub_budget.warning_suffix()
        assert suffix is not None
        assert "exhausted" in suffix.lower()

    def test_three_sub_ravns_share_global_pool(self):
        """Team of 3 sub-ravns all consume from the same global budget object."""
        shared = IterationBudget(total=90)
        for _ in range(3):
            shared.consume(10)  # each sub-ravn does 10 iterations
        assert shared.consumed == 30
        assert shared.remaining == 60

    def test_sub_ravn_ceiling_does_not_exceed_parent(self):
        """When global pool is tighter than ceiling, global limit governs."""
        sub = IterationBudget(total=10, task_ceiling=50)
        sub.consume(8)
        # global_remaining=2, task_remaining=42 → min(2, 42) = 2
        assert sub.remaining == 2


# ---------------------------------------------------------------------------
# Permission scoping
# ---------------------------------------------------------------------------


class TestPermissionScoping:
    def test_spawn_config_read_only_mode(self):
        config = SpawnConfig(
            persona="researcher",
            caps=["web_search"],
            permission_mode="read_only",
        )
        assert config.permission_mode == "read_only"

    def test_spawn_config_workspace_write_mode(self):
        config = SpawnConfig(
            persona="coder",
            caps=["bash", "file_write"],
            permission_mode="workspace_write",
        )
        assert config.permission_mode == "workspace_write"

    def test_spawn_config_full_access_mode(self):
        config = SpawnConfig(
            persona="admin",
            caps=[],
            permission_mode="full_access",
        )
        assert config.permission_mode == "full_access"

    @pytest.mark.asyncio
    async def test_spawn_passes_default_workspace_write_permission(self):
        """task_create spawn=True defaults to workspace_write permission_mode."""
        dl = _make_drive_loop()
        spawn_adapter = _FakeSpawnAdapter(peer_ids=["p-write"])
        mesh = AsyncMock()
        mesh.send = AsyncMock(return_value={"status": "accepted", "task_id": "t-write"})

        tool = TaskCreateTool(
            drive_loop=dl,
            mesh=mesh,
            discovery=_FakeDiscovery({}),
            spawn_adapter=spawn_adapter,
        )
        await tool.execute({"prompt": "work", "title": "write task", "spawn": True})

        assert spawn_adapter.spawned_configs[0].permission_mode == "workspace_write"

    @pytest.mark.asyncio
    async def test_spawn_caps_forwarded_to_spawn_config(self):
        """Required caps from task_create are forwarded to SpawnConfig."""
        dl = _make_drive_loop()
        spawn_adapter = _FakeSpawnAdapter(peer_ids=["p-cap"])
        mesh = AsyncMock()
        mesh.send = AsyncMock(return_value={"status": "accepted", "task_id": "t-cap"})

        tool = TaskCreateTool(
            drive_loop=dl,
            mesh=mesh,
            discovery=_FakeDiscovery({}),
            spawn_adapter=spawn_adapter,
        )
        await tool.execute(
            {
                "prompt": "use gpu",
                "title": "gpu task",
                "spawn": True,
                "required_caps": ["gpu", "pytorch"],
            }
        )

        assert spawn_adapter.spawned_configs[0].caps == ["gpu", "pytorch"]

    @pytest.mark.asyncio
    async def test_spawn_max_concurrent_tasks_is_one(self):
        """Spawned instances get max_concurrent_tasks=1 (no sub-parallelism by default)."""
        dl = _make_drive_loop()
        spawn_adapter = _FakeSpawnAdapter(peer_ids=["p-1"])
        mesh = AsyncMock()
        mesh.send = AsyncMock(return_value={"status": "accepted", "task_id": "t-1"})

        tool = TaskCreateTool(
            drive_loop=dl,
            mesh=mesh,
            discovery=_FakeDiscovery({}),
            spawn_adapter=spawn_adapter,
        )
        await tool.execute({"prompt": "work", "title": "task", "spawn": True})

        assert spawn_adapter.spawned_configs[0].max_concurrent_tasks == 1


# ---------------------------------------------------------------------------
# Epistemic sovereignty
# ---------------------------------------------------------------------------


class TestEpistemicSovereignty:
    def test_each_task_has_independent_session_id(self):
        """No two tasks share a session_id."""
        tasks = [_make_agent_task(f"task-{i}") for i in range(10)]
        session_ids = {t.session_id for t in tasks}
        assert len(session_ids) == 10

    def test_sub_task_session_distinct_from_parent_session(self):
        """Sub-ravn session_id is never equal to the parent interactive session."""
        parent_session = "user-session-abc123"
        sub_task = _make_agent_task("task_20260407_xyz789")
        assert sub_task.session_id != parent_session

    def test_shared_context_is_explicit_not_ambient(self):
        """SharedContext must be explicitly constructed and injected — not inherited."""
        ctx = SharedContext(data={"workspace_root": "/project"})
        assert ctx.data == {"workspace_root": "/project"}
        empty = SharedContext()
        assert empty.data == {}

    def test_agent_task_has_no_shared_context_attribute(self):
        """AgentTask carries no SharedContext — it must be wired externally."""
        task = _make_agent_task("sovereign-task")
        assert not hasattr(task, "shared_context")

    def test_daemon_prefix_identifies_sub_ravn_sessions(self):
        """Sub-ravn session IDs use 'daemon_' prefix for log traceability."""
        task = _make_agent_task("task_20260407_aabbcc")
        assert task.session_id.startswith("daemon_")

    @pytest.mark.asyncio
    async def test_tasks_run_in_independent_agent_instances(self):
        """Each task gets a fresh agent instance — no state sharing between tasks."""
        task_id_to_instance: dict[str, object] = {}
        done_count = [0]
        all_done = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            instance = MagicMock()

            async def _run(prompt: str) -> None:
                task_id_to_instance[task_id] = instance
                done_count[0] += 1
                if done_count[0] >= 2:
                    all_done.set()

            instance.run_turn = _run
            return instance

        dl = _make_drive_loop(agent_factory=_factory)
        for i in range(2):
            await dl.enqueue(_make_agent_task(f"sovereign-{i}"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(all_done.wait(), timeout=3.0)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        # Both tasks were executed with distinct instances
        assert "sovereign-0" in task_id_to_instance
        assert "sovereign-1" in task_id_to_instance
        assert task_id_to_instance["sovereign-0"] is not task_id_to_instance["sovereign-1"]


# ---------------------------------------------------------------------------
# Team simulation: Mode 1 parallel tasks
# ---------------------------------------------------------------------------


class TestTeamSimulation:
    @pytest.mark.asyncio
    async def test_team_of_three_all_complete(self):
        """Parent creates 3 tasks; all execute and complete independently."""
        executed: list[str] = []
        done_count = [0]
        all_done = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                executed.append(task_id)
                done_count[0] += 1
                if done_count[0] >= 3:
                    all_done.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(max_concurrent=3, agent_factory=_factory)
        for i in range(3):
            await dl.enqueue(_make_agent_task(f"team-task-{i}"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(all_done.wait(), timeout=5.0)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        assert done_count[0] >= 3

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """With max_concurrent=2, no more than 2 tasks run simultaneously."""
        concurrent = [0]
        peak = [0]
        done_count = [0]
        all_done = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                concurrent[0] += 1
                peak[0] = max(peak[0], concurrent[0])
                await asyncio.sleep(0.05)
                concurrent[0] -= 1
                done_count[0] += 1
                if done_count[0] >= 4:
                    all_done.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(max_concurrent=2, agent_factory=_factory)
        for i in range(4):
            await dl.enqueue(_make_agent_task(f"concurrent-{i}"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(all_done.wait(), timeout=5.0)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        assert peak[0] <= 2

    @pytest.mark.asyncio
    async def test_task_collect_returns_complete_after_task_finishes(self):
        """TaskCollectTool reports complete=True once the task is no longer active."""
        done = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                done.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory)
        await dl.enqueue(_make_agent_task("collect-me"))

        collect_tool = TaskCollectTool(drive_loop=dl, poll_interval_s=0.01)

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(done.wait(), timeout=3.0)
            await asyncio.sleep(0.05)  # let done-callback clean up

            result = await collect_tool.execute({"task_id": "collect-me", "timeout_s": 2.0})
            assert not result.is_error
            data = json.loads(result.content)
            assert data["status"] == "complete"
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)


# ---------------------------------------------------------------------------
# Error handling: sub-task failure → parent continues
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_failing_task_does_not_block_next_task(self):
        """A failing task does not prevent subsequent tasks from running."""
        executed: list[str] = []
        success_done = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                executed.append(task_id)
                if task_id == "fail-first":
                    raise RuntimeError("deliberate failure")
                if task_id == "succeed-second":
                    success_done.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory)
        await dl.enqueue(_make_agent_task("fail-first"))
        await dl.enqueue(_make_agent_task("succeed-second"))

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(success_done.wait(), timeout=5.0)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        assert "fail-first" in executed
        assert "succeed-second" in executed

    @pytest.mark.asyncio
    async def test_stop_tool_returns_error_on_mesh_failure(self):
        """TaskStopTool returns is_error when the mesh cancel call fails."""
        dl = _make_drive_loop()
        mesh = AsyncMock()
        mesh.send = AsyncMock(side_effect=Exception("peer unreachable"))
        remote_tasks = {"remote-fail": "peer-gone"}
        tool = TaskStopTool(drive_loop=dl, mesh=mesh, remote_tasks=remote_tasks)
        result = await tool.execute({"task_id": "remote-fail"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_collect_timeout_is_error_not_hang(self):
        """TaskCollectTool returns is_error on timeout — does not block the caller."""
        dl = _make_drive_loop()
        await dl.enqueue(_make_agent_task("stuck-task"))
        tool = TaskCollectTool(drive_loop=dl, poll_interval_s=0.01)

        result = await tool.execute({"task_id": "stuck-task", "timeout_s": 0.1})
        assert result.is_error
        assert "did not complete" in result.content


# ---------------------------------------------------------------------------
# E2E cascade scenarios (mock LLM)
# ---------------------------------------------------------------------------


class TestE2EDelegation:
    @pytest.mark.asyncio
    async def test_simple_delegation_create_then_collect(self):
        """E2E: parent calls task_create → task runs → parent calls task_collect → result."""
        done = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                done.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory)
        tools = build_cascade_tools(drive_loop=dl)
        create_tool = next(t for t in tools if t.name == "task_create")
        collect_tool = next(t for t in tools if t.name == "task_collect")
        collect_tool._poll_interval_s = 0.01

        create_result = await create_tool.execute(
            {
                "prompt": "Summarise the project status",
                "title": "Status summary",
            }
        )
        assert not create_result.is_error
        task_data = json.loads(create_result.content)
        task_id = task_data["task_id"]
        assert task_data["location"] == "local"

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(done.wait(), timeout=3.0)
            await asyncio.sleep(0.05)

            collect_result = await collect_tool.execute({"task_id": task_id, "timeout_s": 2.0})
            assert not collect_result.is_error
            assert json.loads(collect_result.content)["status"] == "complete"
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_parallel_team_three_tasks_all_complete(self):
        """E2E: parent creates team of 3 tasks; all execute; parent collects each."""
        done_count = [0]
        all_done = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                done_count[0] += 1
                if done_count[0] >= 3:
                    all_done.set()

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(max_concurrent=3, agent_factory=_factory)
        tools = build_cascade_tools(drive_loop=dl)
        create_tool = next(t for t in tools if t.name == "task_create")
        collect_tool = next(t for t in tools if t.name == "task_collect")
        collect_tool._poll_interval_s = 0.01

        task_ids: list[str] = []
        for i in range(3):
            r = await create_tool.execute(
                {
                    "prompt": f"Analyse component {i}",
                    "title": f"Component {i}",
                }
            )
            assert not r.is_error
            task_ids.append(json.loads(r.content)["task_id"])

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(all_done.wait(), timeout=5.0)
            await asyncio.sleep(0.05)

            for tid in task_ids:
                r = await collect_tool.execute({"task_id": tid, "timeout_s": 2.0})
                assert not r.is_error
                assert json.loads(r.content)["status"] == "complete"
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_cascading_failure_parent_observes_unknown_status(self):
        """Sub-ravn fails → task status is 'unknown' → parent can detect and handle."""
        failed = asyncio.Event()

        def _factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            mock = MagicMock()

            async def _run(prompt: str) -> None:
                failed.set()
                raise RuntimeError("sub-ravn crashed")

            mock.run_turn = _run
            return mock

        dl = _make_drive_loop(agent_factory=_factory)
        task = _make_agent_task("crash-task")
        await dl.enqueue(task)

        status_tool = TaskStatusTool(drive_loop=dl)

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(failed.wait(), timeout=3.0)
            await asyncio.sleep(0.05)

            result = await status_tool.execute({"task_id": "crash-task"})
            data = json.loads(result.content)
            # "unknown" = task no longer active (completed or failed)
            assert data["status"] == "unknown"
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_budget_pressure_team_reduces_parent_remaining(self):
        """Team of sub-ravns consuming shared budget reduces parent remaining budget."""
        shared_budget = IterationBudget(total=90)

        # Three sub-ravns each consume from the shared budget
        shared_budget.consume(18)  # expensive sub-ravn
        shared_budget.consume(5)  # normal sub-ravn
        shared_budget.consume(5)  # normal sub-ravn

        assert shared_budget.consumed == 28
        assert shared_budget.remaining == 62
        assert not shared_budget.exhausted

    @pytest.mark.asyncio
    async def test_e2e_remote_delegation_parent_collects_result(self):
        """E2E: parent delegates via mesh → polls until complete → collects output."""
        dl = _make_drive_loop()
        poll_count = [0]

        async def _mesh_send(target_peer_id, message, **kwargs):  # noqa: ANN001
            msg_type = message.get("type")
            if msg_type == "task_dispatch":
                return {"status": "accepted", "task_id": message["task"]["task_id"]}
            if msg_type == "task_status":
                poll_count[0] += 1
                if poll_count[0] < 2:
                    return {"task_id": message["task_id"], "status": "running"}
                return {"task_id": message["task_id"], "status": "complete"}
            if msg_type == "task_result":
                return {
                    "task_id": message["task_id"],
                    "result": "analysis done",
                    "status": "complete",
                }
            return {"error": "unknown message type"}

        mesh = AsyncMock()
        mesh.send = AsyncMock(side_effect=_mesh_send)
        peer = _FakePeer("remote-peer", status="idle")
        discovery = _FakeDiscovery({"remote-peer": peer})

        tools = build_cascade_tools(drive_loop=dl, mesh=mesh, discovery=discovery)
        create_tool = next(t for t in tools if t.name == "task_create")
        collect_tool = next(t for t in tools if t.name == "task_collect")
        collect_tool._poll_interval_s = 0.01

        create_result = await create_tool.execute(
            {
                "prompt": "analyse logs on remote peer",
                "title": "Remote log analysis",
            }
        )
        assert not create_result.is_error
        data = json.loads(create_result.content)
        assert data["location"] == "remote-peer"
        task_id = data["task_id"]

        collect_result = await collect_tool.execute({"task_id": task_id, "timeout_s": 5.0})
        assert not collect_result.is_error
        result = json.loads(collect_result.content)
        assert result.get("result") == "analysis done"

    @pytest.mark.asyncio
    async def test_task_list_shows_team_tasks(self):
        """TaskListTool shows all tasks in the team (local active + queued)."""
        dl = _make_drive_loop()
        # Put one active and one queued
        dl._active_tasks["active-team-task"] = MagicMock()
        await dl.enqueue(_make_agent_task("queued-team-task"))

        tool = TaskListTool(drive_loop=dl)
        result = await tool.execute({})
        data = json.loads(result.content)
        assert "active-team-task" in data["local"]["active"]
        assert "queued-team-task" in data["local"]["queued"]
