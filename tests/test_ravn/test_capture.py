"""Tests for NIU-545 — CaptureChannel, TaskResultStore, and task progress visibility.

Coverage targets:
- TaskResultStore: capacity eviction, event accumulation, output set on RESPONSE
- CaptureChannel: event accumulation, output/status set on RESPONSE/ERROR
- CaptureChannel: surface_triggered and response_text contract (same as SilentChannel)
- DriveLoop.task_status(include_progress=True) returns events while running
- DriveLoop.get_result() returns TaskResult
- DriveLoop._run_task() uses CaptureChannel when cascade.enabled
- TaskCollectTool returns actual output text from TaskResultStore for local task
- Integration: coordinator creates 2 local tasks, polls progress, collects output
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.channels.capture import (
    CaptureChannel,
    CapturedEvent,
    TaskResultStore,
)
from ravn.adapters.tools.cascade_tools import TaskCollectTool, TaskStatusTool
from ravn.config import InitiativeConfig
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import AgentTask, OutputMode
from ravn.drive_loop import DriveLoop

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: RavnEventType, payload: dict) -> RavnEvent:
    return RavnEvent(
        type=event_type,
        source="test",
        payload=payload,
        timestamp=datetime.now(UTC),
        urgency=0.1,
        correlation_id="corr-1",
        session_id="sess-1",
        task_id="task-1",
    )


def _make_drive_loop(cascade_enabled: bool = False) -> DriveLoop:
    agent_factory = MagicMock(return_value=AsyncMock())
    cfg = InitiativeConfig(enabled=True, max_concurrent_tasks=3, task_queue_max=50)
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = cascade_enabled
    settings.budget.daily_cap_usd = 1.0
    settings.budget.warn_at_percent = 80
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    return DriveLoop(agent_factory=agent_factory, config=cfg, settings=settings)


def _make_agent_task(task_id: str = "task-001", triggered_by: str = "test") -> AgentTask:
    return AgentTask(
        task_id=task_id,
        title="test task",
        initiative_context="do something",
        triggered_by=triggered_by,
        output_mode=OutputMode.SILENT,
    )


# ---------------------------------------------------------------------------
# TaskResultStore tests
# ---------------------------------------------------------------------------


class TestTaskResultStore:
    def test_start_creates_running_result(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        result = store.get("t1")
        assert result is not None
        assert result.task_id == "t1"
        assert result.status == "running"
        assert result.output == ""
        assert result.events == []
        assert result.triggered_by == "tester"
        assert result.completed_at is None

    def test_append_event(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        event = CapturedEvent(
            type="thought", summary="thinking: hello", timestamp=datetime.now(UTC)
        )
        store.append_event("t1", event)
        result = store.get("t1")
        assert len(result.events) == 1
        assert result.events[0].summary == "thinking: hello"

    def test_append_event_unknown_task_noop(self):
        store = TaskResultStore()
        event = CapturedEvent(type="thought", summary="x", timestamp=datetime.now(UTC))
        store.append_event("no-such-task", event)  # must not raise

    def test_set_output_marks_complete(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        store.set_output("t1", "The answer is 42.")
        result = store.get("t1")
        assert result.output == "The answer is 42."
        assert result.status == "complete"
        assert result.completed_at is not None

    def test_set_status_failed(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        store.set_status("t1", "failed")
        result = store.get("t1")
        assert result.status == "failed"
        assert result.completed_at is not None

    def test_set_status_cancelled(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        store.set_status("t1", "cancelled")
        result = store.get("t1")
        assert result.status == "cancelled"

    def test_get_returns_none_for_unknown(self):
        store = TaskResultStore()
        assert store.get("nonexistent") is None

    def test_active_ids_filters_running(self):
        store = TaskResultStore()
        store.start("t1", "a")
        store.start("t2", "b")
        store.set_status("t2", "complete")
        assert store.active_ids() == ["t1"]

    def test_capacity_eviction_removes_oldest(self):
        store = TaskResultStore(capacity=3)
        store.start("t1", "a")
        store.start("t2", "b")
        store.start("t3", "c")
        # All three fit
        assert store.get("t1") is not None
        # Adding a fourth evicts t1
        store.start("t4", "d")
        assert store.get("t1") is None
        assert store.get("t2") is not None
        assert store.get("t3") is not None
        assert store.get("t4") is not None

    def test_capacity_eviction_continues_in_order(self):
        store = TaskResultStore(capacity=2)
        store.start("a", "x")
        store.start("b", "x")
        store.start("c", "x")  # evicts a
        assert store.get("a") is None
        store.start("d", "x")  # evicts b
        assert store.get("b") is None
        assert store.get("c") is not None
        assert store.get("d") is not None

    def test_restarting_same_task_id_overwrites(self):
        store = TaskResultStore()
        store.start("t1", "first")
        store.set_output("t1", "first output")
        store.start("t1", "second")
        result = store.get("t1")
        assert result.output == ""
        assert result.status == "running"
        assert result.triggered_by == "second"


# ---------------------------------------------------------------------------
# CaptureChannel tests
# ---------------------------------------------------------------------------


class TestCaptureChannel:
    @pytest.mark.asyncio
    async def test_accumulates_thought_event(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        ch = CaptureChannel("t1", store)
        event = _make_event(RavnEventType.THOUGHT, {"text": "I am thinking about this"})
        await ch.emit(event)
        result = store.get("t1")
        assert len(result.events) == 1
        assert "thinking:" in result.events[0].summary

    @pytest.mark.asyncio
    async def test_accumulates_tool_start_event(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        ch = CaptureChannel("t1", store)
        event = _make_event(
            RavnEventType.TOOL_START, {"tool_name": "bash", "input": {"command": "ls -la"}}
        )
        await ch.emit(event)
        result = store.get("t1")
        assert "tool: bash(" in result.events[0].summary

    @pytest.mark.asyncio
    async def test_accumulates_tool_result_event(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        ch = CaptureChannel("t1", store)
        event = _make_event(RavnEventType.TOOL_RESULT, {"result": "file1.txt\nfile2.txt"})
        await ch.emit(event)
        result = store.get("t1")
        assert result.events[0].summary.startswith("→ ")

    @pytest.mark.asyncio
    async def test_response_sets_output_and_marks_complete(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        ch = CaptureChannel("t1", store)
        event = _make_event(RavnEventType.RESPONSE, {"text": "Here is the answer."})
        await ch.emit(event)
        result = store.get("t1")
        assert result.output == "Here is the answer."
        assert result.status == "complete"
        assert ch.response_text == "Here is the answer."

    @pytest.mark.asyncio
    async def test_error_event_marks_failed(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        ch = CaptureChannel("t1", store)
        event = _make_event(RavnEventType.ERROR, {"message": "something went wrong"})
        await ch.emit(event)
        result = store.get("t1")
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_surface_triggered_detected(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        ch = CaptureChannel("t1", store)
        assert not ch.surface_triggered
        event = _make_event(RavnEventType.RESPONSE, {"text": "[SURFACE] Important update!"})
        await ch.emit(event)
        assert ch.surface_triggered
        assert ch.response_text == "[SURFACE] Important update!"

    @pytest.mark.asyncio
    async def test_surface_not_triggered_for_normal_response(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        ch = CaptureChannel("t1", store)
        event = _make_event(RavnEventType.RESPONSE, {"text": "Normal response"})
        await ch.emit(event)
        assert not ch.surface_triggered

    @pytest.mark.asyncio
    async def test_multiple_events_accumulated(self):
        store = TaskResultStore()
        store.start("t1", "tester")
        ch = CaptureChannel("t1", store)
        events = [
            _make_event(RavnEventType.THOUGHT, {"text": "step 1"}),
            _make_event(RavnEventType.TOOL_START, {"tool_name": "bash", "input": {}}),
            _make_event(RavnEventType.TOOL_RESULT, {"result": "ok"}),
            _make_event(RavnEventType.RESPONSE, {"text": "done"}),
        ]
        for e in events:
            await ch.emit(e)
        result = store.get("t1")
        assert len(result.events) == 4
        assert result.output == "done"
        assert result.status == "complete"


# ---------------------------------------------------------------------------
# DriveLoop integration with CaptureChannel
# ---------------------------------------------------------------------------


class TestDriveLoopCaptureIntegration:
    @pytest.mark.asyncio
    async def test_get_result_returns_none_when_no_store_entry(self):
        dl = _make_drive_loop(cascade_enabled=False)
        assert dl.get_result("nonexistent") is None

    @pytest.mark.asyncio
    async def test_task_status_include_progress_false_returns_string(self):
        dl = _make_drive_loop()
        status = dl.task_status("nonexistent", include_progress=False)
        assert status == "unknown"
        assert isinstance(status, str)

    @pytest.mark.asyncio
    async def test_task_status_include_progress_true_returns_dict(self):
        dl = _make_drive_loop()
        result = dl.task_status("nonexistent", include_progress=True)
        assert isinstance(result, dict)
        assert result["status"] == "unknown"
        assert result["events"] == []

    @pytest.mark.asyncio
    async def test_task_status_include_progress_returns_events(self):
        dl = _make_drive_loop(cascade_enabled=True)
        # Manually inject into store
        dl._result_store.start("t1", "tester")
        event = CapturedEvent(type="thought", summary="thinking: x", timestamp=datetime.now(UTC))
        dl._result_store.append_event("t1", event)
        # Simulate task in active state
        dl._active_tasks["t1"] = MagicMock()

        result = dl.task_status("t1", include_progress=True)
        assert isinstance(result, dict)
        assert result["status"] == "running"
        assert len(result["events"]) == 1
        assert result["events"][0]["summary"] == "thinking: x"

    @pytest.mark.asyncio
    async def test_run_task_uses_capture_channel_when_cascade_enabled(self):
        """When cascade.enabled, DriveLoop._run_task registers a result in the store."""
        captured_channels = []
        response_sent = asyncio.Event()

        async def _mock_run_turn(prompt: str) -> None:
            # Emit a RESPONSE event through the channel that was captured
            if captured_channels:
                ch = captured_channels[0]
                event = _make_event(RavnEventType.RESPONSE, {"text": "task output"})
                await ch.emit(event)
            response_sent.set()

        mock_agent = MagicMock()
        mock_agent.run_turn = _mock_run_turn

        def _agent_factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            captured_channels.append(channel)
            return mock_agent

        cfg = InitiativeConfig(enabled=True, max_concurrent_tasks=3, task_queue_max=50)
        settings = MagicMock()
        settings.skuld.enabled = False
        settings.cascade.enabled = True
        dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)

        task = _make_agent_task("capture-task-1")
        await dl.enqueue(task)

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(response_sent.wait(), timeout=5.0)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        assert isinstance(captured_channels[0], CaptureChannel)
        result = dl.get_result("capture-task-1")
        assert result is not None
        assert result.output == "task output"
        assert result.status == "complete"

    @pytest.mark.asyncio
    async def test_run_task_uses_silent_channel_when_cascade_disabled(self):
        """When cascade.enabled=False, DriveLoop._run_task uses SilentChannel."""
        from ravn.adapters.channels.silent import SilentChannel  # noqa: PLC0415

        captured_channels = []
        finished = asyncio.Event()

        async def _mock_run_turn(prompt: str) -> None:
            finished.set()

        mock_agent = MagicMock()
        mock_agent.run_turn = _mock_run_turn

        def _agent_factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            captured_channels.append(channel)
            return mock_agent

        cfg = InitiativeConfig(enabled=True, max_concurrent_tasks=1, task_queue_max=10)
        settings = MagicMock()
        settings.skuld.enabled = False
        settings.cascade.enabled = False
        dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)

        task = _make_agent_task("silent-task-1")
        await dl.enqueue(task)

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(finished.wait(), timeout=5.0)
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        assert isinstance(captured_channels[0], SilentChannel)

    @pytest.mark.asyncio
    async def test_run_task_sets_failed_status_on_exception(self):
        """When run_turn raises, DriveLoop sets task status to failed in the store."""
        finished = asyncio.Event()

        async def _mock_run_turn_raises(prompt: str) -> None:
            finished.set()
            raise RuntimeError("agent crashed")

        mock_agent = MagicMock()
        mock_agent.run_turn = _mock_run_turn_raises

        def _agent_factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
            return mock_agent

        cfg = InitiativeConfig(enabled=True, max_concurrent_tasks=1, task_queue_max=10)
        settings = MagicMock()
        settings.skuld.enabled = False
        settings.cascade.enabled = True
        dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)

        task = _make_agent_task("failing-task-1")
        await dl.enqueue(task)

        loop_task = asyncio.create_task(dl.run())
        try:
            await asyncio.wait_for(finished.wait(), timeout=5.0)
            await asyncio.sleep(0.05)  # let _run_task finish the except block
        finally:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)

        result = dl.get_result("failing-task-1")
        assert result is not None
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# TaskStatusTool with include_progress
# ---------------------------------------------------------------------------


class TestTaskStatusToolIncludeProgress:
    @pytest.mark.asyncio
    async def test_include_progress_false_returns_status_string(self):
        dl = _make_drive_loop()
        tool = TaskStatusTool(drive_loop=dl)
        result = await tool.execute({"task_id": "nonexistent"})
        data = json.loads(result.content)
        assert data["status"] == "unknown"
        assert "events" not in data

    @pytest.mark.asyncio
    async def test_include_progress_true_returns_events(self):
        dl = _make_drive_loop(cascade_enabled=True)
        dl._result_store.start("t1", "test")
        event = CapturedEvent(
            type="thought", summary="thinking: test thought", timestamp=datetime.now(UTC)
        )
        dl._result_store.append_event("t1", event)
        # Simulate running task
        dl._active_tasks["t1"] = MagicMock()

        tool = TaskStatusTool(drive_loop=dl)
        result = await tool.execute({"task_id": "t1", "include_progress": True})
        data = json.loads(result.content)
        assert data["task_id"] == "t1"
        assert data["status"] == "running"
        assert len(data["events"]) == 1
        assert data["events"][0]["summary"] == "thinking: test thought"

    @pytest.mark.asyncio
    async def test_include_progress_passes_to_mesh_for_remote_task(self):
        dl = _make_drive_loop()
        mesh = MagicMock()
        mesh.send = AsyncMock(
            return_value={"task_id": "remote-t1", "status": "running", "events": []}
        )
        remote_tasks = {"remote-t1": "peer-abc"}
        tool = TaskStatusTool(drive_loop=dl, mesh=mesh, remote_tasks=remote_tasks)
        result = await tool.execute({"task_id": "remote-t1", "include_progress": True})
        data = json.loads(result.content)
        assert data["status"] == "running"
        # include_progress should be passed through to mesh
        call_args = mesh.send.call_args
        assert call_args[1]["message"]["include_progress"] is True


# ---------------------------------------------------------------------------
# TaskCollectTool returns real output for local tasks
# ---------------------------------------------------------------------------


class TestTaskCollectToolOutput:
    @pytest.mark.asyncio
    async def test_collect_returns_output_from_result_store(self):
        dl = _make_drive_loop(cascade_enabled=True)
        dl._result_store.start("t1", "test")
        dl._result_store.set_output("t1", "The final answer is 42.")

        tool = TaskCollectTool(
            drive_loop=dl,
            poll_interval_s=0.01,
            default_timeout_s=5.0,
        )
        result = await tool.execute({"task_id": "t1", "timeout_s": 5.0})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["task_id"] == "t1"
        assert data["status"] == "complete"
        assert data["output"] == "The final answer is 42."
        assert "event_count" in data

    @pytest.mark.asyncio
    async def test_collect_returns_empty_output_when_no_result(self):
        dl = _make_drive_loop()
        # task is "unknown" so poll exits immediately
        tool = TaskCollectTool(
            drive_loop=dl,
            poll_interval_s=0.01,
            default_timeout_s=5.0,
        )
        result = await tool.execute({"task_id": "no-such-task", "timeout_s": 5.0})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["task_id"] == "no-such-task"
        assert data["output"] == ""

    @pytest.mark.asyncio
    async def test_collect_polls_until_task_completes(self):
        dl = _make_drive_loop(cascade_enabled=True)
        dl._result_store.start("t1", "test")

        # Simulate task completing after a brief delay
        async def _complete_later():
            await asyncio.sleep(0.05)
            dl._result_store.set_output("t1", "async result")

        asyncio.create_task(_complete_later())
        # Register as active initially so poll loop waits
        dummy_asyncio_task = asyncio.create_task(asyncio.sleep(10))
        dl._active_tasks["t1"] = dummy_asyncio_task

        async def _remove_from_active_after_delay():
            await asyncio.sleep(0.06)
            dl._active_tasks.pop("t1", None)
            dummy_asyncio_task.cancel()

        asyncio.create_task(_remove_from_active_after_delay())

        tool = TaskCollectTool(
            drive_loop=dl,
            poll_interval_s=0.02,
            default_timeout_s=5.0,
        )
        result = await tool.execute({"task_id": "t1", "timeout_s": 5.0})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["output"] == "async result"

    @pytest.mark.asyncio
    async def test_collect_event_count_matches(self):
        dl = _make_drive_loop(cascade_enabled=True)
        dl._result_store.start("t1", "test")
        for i in range(5):
            ev = CapturedEvent(
                type="thought", summary=f"thinking: step {i}", timestamp=datetime.now(UTC)
            )
            dl._result_store.append_event("t1", ev)
        dl._result_store.set_output("t1", "done")

        tool = TaskCollectTool(
            drive_loop=dl,
            poll_interval_s=0.01,
            default_timeout_s=5.0,
        )
        result = await tool.execute({"task_id": "t1"})
        data = json.loads(result.content)
        # set_output only sets output text; events are added via CaptureChannel.emit
        assert data["event_count"] == 5


# ---------------------------------------------------------------------------
# Integration: coordinator creates 2 local tasks, polls progress, collects output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_two_local_tasks_progress_and_collect():
    """Coordinator creates 2 local tasks, polls progress on both, collects output.

    One task completes before the other finishes.  Verifies that:
    - task_status(include_progress=True) returns accumulated events while running
    - task_collect returns the actual output for the first-completed task
    - The second task can still be collected after the first
    """
    task_1 = "integ-task-1"
    task_2 = "integ-task-2"

    t1_barrier = asyncio.Event()
    t2_barrier = asyncio.Event()
    t1_started = asyncio.Event()
    t2_started = asyncio.Event()

    channel_map: dict[str, CaptureChannel] = {}

    async def _run_task1(prompt: str) -> None:
        t1_started.set()
        await t1_barrier.wait()

    async def _run_task2(prompt: str) -> None:
        t2_started.set()
        await t2_barrier.wait()

    run_turn_map = {task_1: _run_task1, task_2: _run_task2}

    def _agent_factory(channel, task_id=None, persona=None, triggered_by=None):  # noqa: ANN001
        if task_id is not None:
            channel_map[task_id] = channel
        agent = MagicMock()
        agent.run_turn = run_turn_map.get(task_id, AsyncMock())
        return agent

    cfg = InitiativeConfig(enabled=True, max_concurrent_tasks=3, task_queue_max=50)
    settings = MagicMock()
    settings.skuld.enabled = False
    settings.cascade.enabled = True
    dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)

    # Enqueue both tasks
    for task_id in [task_1, task_2]:
        task = AgentTask(
            task_id=task_id,
            title=f"Integration task {task_id}",
            initiative_context="do work",
            triggered_by="integration-test",
            output_mode=OutputMode.SILENT,
        )
        await dl.enqueue(task)

    loop_task = asyncio.create_task(dl.run())

    try:
        # Wait for both tasks to be running
        await asyncio.wait_for(asyncio.gather(t1_started.wait(), t2_started.wait()), timeout=5.0)

        # Simulate events on both tasks while running
        ch1 = channel_map[task_1]
        ch2 = channel_map[task_2]

        await ch1.emit(_make_event(RavnEventType.THOUGHT, {"text": "task1 thinking"}))
        await ch2.emit(_make_event(RavnEventType.THOUGHT, {"text": "task2 thinking"}))

        # Poll progress on task 1 while it's running
        progress1 = dl.task_status(task_1, include_progress=True)
        assert isinstance(progress1, dict)
        assert progress1["status"] == "running"
        assert len(progress1["events"]) == 2
        assert progress1["events"][0]["type"] == "task_started"
        assert "task1 thinking" in progress1["events"][1]["summary"]

        # Poll progress on task 2 while it's running
        progress2 = dl.task_status(task_2, include_progress=True)
        assert isinstance(progress2, dict)
        assert progress2["status"] == "running"
        assert len(progress2["events"]) == 2
        assert progress2["events"][0]["type"] == "task_started"

        # Complete task 1 first — emit RESPONSE then release barrier
        await ch1.emit(_make_event(RavnEventType.RESPONSE, {"text": "task1 completed output"}))
        t1_barrier.set()

        # Wait for task 1 to be removed from active tasks
        for _ in range(50):
            if task_1 not in dl._active_tasks:
                break
            await asyncio.sleep(0.05)

        # Collect task 1 output before task 2 finishes
        tool = TaskCollectTool(drive_loop=dl, poll_interval_s=0.01, default_timeout_s=5.0)
        collect1 = await tool.execute({"task_id": task_1, "timeout_s": 5.0})
        assert not collect1.is_error
        data1 = json.loads(collect1.content)
        assert data1["output"] == "task1 completed output"
        assert data1["status"] == "complete"

        # Task 2 still running
        assert dl.task_status(task_2) == "running"

        # Now complete task 2
        await ch2.emit(_make_event(RavnEventType.RESPONSE, {"text": "task2 final output"}))
        t2_barrier.set()

        # Wait for task 2 to finish
        for _ in range(50):
            if task_2 not in dl._active_tasks:
                break
            await asyncio.sleep(0.05)

        collect2 = await tool.execute({"task_id": task_2, "timeout_s": 5.0})
        assert not collect2.is_error
        data2 = json.loads(collect2.content)
        assert data2["output"] == "task2 final output"

    finally:
        t1_barrier.set()
        t2_barrier.set()
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)
