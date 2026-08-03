"""Tests for DriveLoop channel construction with mesh (NIU-634).

Verifies that MeshActivityChannel is included in the composite channel
when mesh is enabled, and that the channel construction falls back
correctly when mesh is not available.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.channels.capture import CapturedEvent
from ravn.adapters.channels.composite import CompositeChannel
from ravn.adapters.channels.mesh_channel import MeshActivityChannel
from ravn.adapters.channels.silent import SilentChannel
from ravn.adapters.channels.task_context import TaskContextChannel
from ravn.adapters.personas.loader import PersonaConfig, PersonaProduces
from ravn.config import InitiativeConfig
from ravn.domain.exceptions import LLMError
from ravn.domain.models import AgentTask, OutputMode
from ravn.drive_loop import DriveLoop


def _make_task(
    task_id: str = "t1",
    *,
    output_mode: OutputMode = OutputMode.SILENT,
) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        title="test",
        initiative_context="do it",
        triggered_by="test",
        output_mode=output_mode,
    )


def _make_drive_loop_with_mesh(
    *,
    cascade_enabled: bool = False,
    skuld_enabled: bool = False,
    mesh_enabled: bool = True,
    peer_id: str = "ravn-peer",
) -> tuple[DriveLoop, list]:
    """Build a DriveLoop with mesh configured; return (loop, captured_channels)."""
    captured: list = []

    def _agent_factory(channel, task_id, persona, triggered_by):
        captured.append(channel)
        agent = AsyncMock()
        fake_usage = MagicMock(input_tokens=0, output_tokens=0)
        agent.run_turn = AsyncMock(return_value=MagicMock(usage=fake_usage, cost_usd=0.0))
        return agent

    cfg = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=1,
        task_queue_max=10,
        queue_journal_path="/proc/no_such_dir/queue.json",
    )
    settings = MagicMock()
    settings.skuld.enabled = skuld_enabled
    settings.cascade.enabled = cascade_enabled
    settings.mesh.enabled = mesh_enabled
    settings.mesh.own_peer_id = peer_id
    settings.gateway.channels.http.resident_hud_enabled = True
    settings.gateway.channels.http.resident_hud_task_context_max_chars = 4000
    settings.gateway.channels.http.resident_hud_recent_tasks = 5
    settings.budget.daily_cap_usd = 100.0
    settings.budget.warn_at_percent = 80
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0
    settings.gateway.channels.http.resident_hud_task_context_max_chars = 4000
    settings.effective_model.return_value = "test-model"

    dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)
    return dl, captured


class TestDriveLoopChannelConstruction:
    """Verify channel composition logic in _run_task."""

    @pytest.mark.asyncio
    async def test_silent_mode_does_not_use_mesh_channel(self):
        """Silent work remains local even when a mesh is available."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=False)
        mock_mesh = AsyncMock()
        dl._mesh = mock_mesh

        await dl._run_task(_make_task())

        assert len(captured) == 1
        assert isinstance(captured[0], SilentChannel)

    @pytest.mark.asyncio
    async def test_ambient_mode_uses_mesh_channel(self):
        """Ambient work is routed through the mesh attention path."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=False)
        dl._mesh = AsyncMock()

        await dl._run_task(_make_task(output_mode=OutputMode.AMBIENT))

        channel = captured[0]
        assert isinstance(channel, TaskContextChannel)
        assert isinstance(channel._channel, MeshActivityChannel)

    @pytest.mark.asyncio
    async def test_mesh_disabled_no_cascade_uses_silent(self):
        """Mesh disabled, no cascade → SilentChannel."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=False, mesh_enabled=False)
        dl._mesh = None

        await dl._run_task(_make_task(output_mode=OutputMode.AMBIENT))

        assert isinstance(captured[0], SilentChannel)

    @pytest.mark.asyncio
    async def test_mesh_no_peer_id_uses_silent(self):
        """Mesh enabled but peer_id empty → SilentChannel (no publish target)."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=False, peer_id="")
        dl._mesh = AsyncMock()

        await dl._run_task(_make_task())

        assert isinstance(captured[0], SilentChannel)

    @pytest.mark.asyncio
    async def test_cascade_with_mesh_uses_composite(self):
        """cascade.enabled=True + mesh → CompositeChannel including wrapped mesh activity."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=True)
        mock_mesh = AsyncMock()
        dl._mesh = mock_mesh

        await dl._run_task(_make_task(output_mode=OutputMode.AMBIENT))

        assert isinstance(captured[0], CompositeChannel)
        channel_types = [type(ch) for ch in captured[0]._channels]
        assert TaskContextChannel in channel_types
        wrapped = [ch for ch in captured[0]._channels if isinstance(ch, TaskContextChannel)]
        assert any(isinstance(ch._channel, MeshActivityChannel) for ch in wrapped)

    @pytest.mark.asyncio
    async def test_cascade_without_mesh_uses_capture_only(self):
        """cascade.enabled=True, mesh=None → CaptureChannel only (no composite)."""
        from ravn.adapters.channels.capture import CaptureChannel

        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=True)
        dl._mesh = None

        await dl._run_task(_make_task())

        # With cascade and no mesh/skuld_channel, channel is capture_channel alone
        assert isinstance(captured[0], CaptureChannel)

    def test_resident_hud_status_reports_factual_active_task_and_bounded_activity(self):
        dl, _ = _make_drive_loop_with_mesh(cascade_enabled=True)
        task = _make_task("active-task")
        task.initiative_context = """\
# Signals since your last look — 4 observation(s)

## Observed signals (bounded payload excerpts)

- `status` (warning, workshop): payload=Muninn paused with ErrorCode 3

## Your task

Determine whether the observations require action.
"""
        task.trace_context = {
            "traceparent": "00-56ca7fcf3496a1e9fe7136f55a110c2a-0123456789abcdef-01"
        }
        dl._active_tasks[task.task_id] = MagicMock()
        dl._inflight_tasks[task.task_id] = task
        dl._active_agents[task.task_id] = SimpleNamespace(
            iteration_budget=None,
            prompt_budget_status={
                "estimated_prompt_tokens": 42_000,
                "prompt_budget_tokens": 100_000,
                "output_reserve_tokens": 8_192,
                "context_window_tokens": 131_072,
                "token_estimate_safety_factor": 1.5,
                "compressed": False,
            },
        )
        dl._result_store.start(task.task_id, task.triggered_by)
        dl._result_store.append_event(
            task.task_id,
            CapturedEvent(
                type="tool_start",
                summary="tool: research()",
                timestamp=datetime(2026, 7, 25, tzinfo=UTC),
            ),
        )

        status = dl.resident_hud_status()

        assert status["active_count"] == 1
        assert status["queued_count"] == 0
        assert status["active_tasks"] == [
            {
                "task_id": "active-task",
                "title": "test",
                "triggered_by": "test",
                "persona": "",
                "root_correlation_id": "active-task",
                "case_id": "",
                "turn_index": 0,
                "trace_id": "56ca7fcf3496a1e9fe7136f55a110c2a",
                "status": "running",
                "started_at": dl._result_store.get("active-task").started_at.isoformat(),
                "completed_at": "",
                "input": {
                    "summary": "Signals since your last look — 4 observation(s)",
                    "observations": (
                        "- `status` (warning, workshop): payload=Muninn paused with ErrorCode 3"
                    ),
                    "objective": "Determine whether the observations require action.",
                },
                "progress": {
                    "iteration": 0,
                    "iteration_budget": 0,
                    "tool_calls": 1,
                    "warnings": 0,
                    "prompt": {
                        "estimated_prompt_tokens": 42_000,
                        "prompt_budget_tokens": 100_000,
                        "output_reserve_tokens": 8_192,
                        "context_window_tokens": 131_072,
                        "token_estimate_safety_factor": 1.5,
                        "compressed": False,
                    },
                },
                "events": [
                    {
                        "type": "tool_start",
                        "summary": "tool: research()",
                        "timestamp": "2026-07-25T00:00:00+00:00",
                    }
                ],
            }
        ]
        assert status["recent_tasks"] == []
        assert status["queue_capacity"] == 10
        assert status["queued_tasks"] == []
        assert status["a2a_count"] == 0
        assert status["a2a_tasks"] == []

    def test_resident_hud_status_retains_recent_completed_task(self):
        from datetime import UTC, datetime

        from ravn.adapters.channels.capture import CapturedEvent

        dl, _ = _make_drive_loop_with_mesh(cascade_enabled=True)
        dl._result_store.start(
            "completed-task",
            "resident:scheduled_wake",
            metadata={
                "resident_hud": True,
                "title": "Review the workshop",
                "persona": "Ivaldi",
                "root_correlation_id": "root-completed",
                "case_id": "case-completed",
                "turn_index": 3,
                "trace_id": "trace-completed",
                "input": {
                    "summary": "Review the workshop",
                    "observations": "Printer is idle",
                    "objective": "Decide whether to act",
                },
                "iteration": 2,
                "iteration_budget": 8,
            },
        )
        dl._result_store.append_event(
            "completed-task",
            CapturedEvent(
                type="tool_start",
                summary="tool: research()",
                timestamp=datetime(2026, 7, 26, tzinfo=UTC),
            ),
        )
        dl._result_store.set_output("completed-task", "No action required")

        status = dl.resident_hud_status()

        assert status["active_tasks"] == []
        assert len(status["recent_tasks"]) == 1
        recent = status["recent_tasks"][0]
        assert recent["task_id"] == "completed-task"
        assert recent["title"] == "Review the workshop"
        assert recent["status"] == "complete"
        assert recent["completed_at"]
        assert recent["progress"] == {
            "iteration": 2,
            "iteration_budget": 8,
            "tool_calls": 1,
            "warnings": 0,
            "prompt": {},
        }
        assert recent["events"] == [
            {
                "type": "tool_start",
                "summary": "tool: research()",
                "timestamp": "2026-07-26T00:00:00+00:00",
            }
        ]
        assert status["model"] == "test-model"

    def test_resident_hud_explains_failed_outcomes(self):
        dl, _ = _make_drive_loop_with_mesh(cascade_enabled=True)
        dl._result_store.start(
            "failed-task",
            "signal:durable_window",
            metadata={"resident_hud": True, "title": "Inspect etcd"},
        )
        dl._result_store.set_failure(
            "failed-task",
            "The resident returned an invalid structured outcome.",
            ["resident Valkyrie outcome block is missing"],
        )

        failed = dl.resident_hud_status()["recent_tasks"][0]
        assert failed["status"] == "failed"
        assert failed["failure"] == {
            "reason": "The resident returned an invalid structured outcome.",
            "errors": ["resident Valkyrie outcome block is missing"],
        }
        assert failed["progress"]["warnings"] == 1

    def test_resident_hud_status_describes_waiting_tasks(self):
        dl, _ = _make_drive_loop_with_mesh(cascade_enabled=True)
        task = _make_task("queued-task")
        task.title = "Resident signal window: 3 observation(s)"
        task.triggered_by = "signal:durable_window"
        task.root_correlation_id = "signal-window:workshop:abc"
        task.initiative_context = "# Signals since your last look — 3 observation(s)"
        dl._queue.put_nowait((task.priority, 1, task))

        status = dl.resident_hud_status()

        assert status["queued_count"] == 1
        assert status["queued_tasks"] == [
            {
                "task_id": "queued-task",
                "title": "Resident signal window: 3 observation(s)",
                "triggered_by": "signal:durable_window",
                "root_correlation_id": "signal-window:workshop:abc",
                "created_at": task.created_at.isoformat(),
                "input_summary": "Signals since your last look — 3 observation(s)",
            }
        ]

    def test_resident_hud_status_projects_latest_non_terminal_a2a_task(self):
        dl, _ = _make_drive_loop_with_mesh(cascade_enabled=True)
        parent = _make_task("parent-task")
        dl._active_tasks[parent.task_id] = MagicMock()
        dl._inflight_tasks[parent.task_id] = parent
        dl._result_store.start(parent.task_id, parent.triggered_by)
        observed_at = datetime(2026, 7, 25, 10, 1, tzinfo=UTC)
        dl._result_store.append_event(
            parent.task_id,
            CapturedEvent(
                type="a2a_task",
                summary="A2A task task-1: TASK_STATE_INPUT_REQUIRED",
                timestamp=observed_at,
                details={
                    "operation": "build",
                    "agent_id": "agent-1",
                    "skill_id": "research",
                    "task_id": "task-1",
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "input_required": True,
                    "question": "Which firmware version?",
                    "prompt": "Investigate the observed fault.",
                    "source_tool": "build_tool",
                },
            ),
        )

        status = dl.resident_hud_status()

        assert status["a2a_count"] == 1
        assert status["a2a_tasks"] == [
            {
                "agent_id": "agent-1",
                "skill_id": "research",
                "task_id": "task-1",
                "state": "TASK_STATE_INPUT_REQUIRED",
                "operation": "build",
                "input_required": True,
                "question": "Which firmware version?",
                "prompt": "Investigate the observed fault.",
                "status_message": "",
                "source_tool": "build_tool",
                "parent_task_id": "parent-task",
                "parent_active": True,
                "started_at": observed_at.isoformat(),
                "observed_at": observed_at.isoformat(),
            }
        ]

        dl._result_store.append_event(
            parent.task_id,
            CapturedEvent(
                type="a2a_task",
                summary="A2A task task-1: TASK_STATE_COMPLETED",
                timestamp=datetime(2026, 7, 25, 10, 2, tzinfo=UTC),
                details={
                    "operation": "build",
                    "agent_id": "agent-1",
                    "task_id": "task-1",
                    "state": "TASK_STATE_COMPLETED",
                    "source_tool": "build_tool",
                },
            ),
        )

        assert dl.resident_hud_status()["a2a_tasks"] == []

    def test_record_a2a_activity_captures_the_polled_remote_state(self):
        dl, _ = _make_drive_loop_with_mesh(cascade_enabled=True)
        parent = _make_task("parent-task")
        dl._active_tasks[parent.task_id] = MagicMock()
        dl._inflight_tasks[parent.task_id] = parent
        dl._result_store.start(parent.task_id, parent.triggered_by)

        dl.record_a2a_activity(
            parent_task_id=parent.task_id,
            activity={
                "agent_id": "https://ting.example/.well-known/agent-card.json",
                "skill_id": "tool-builder",
                "task_id": "task-1",
                "state": "TASK_STATE_WORKING",
                "operation": "build",
                "source_tool": "build_tool",
            },
        )

        status = dl.resident_hud_status()

        assert status["a2a_count"] == 1
        assert status["a2a_tasks"][0]["task_id"] == "task-1"
        assert status["a2a_tasks"][0]["state"] == "TASK_STATE_WORKING"
        assert status["a2a_tasks"][0]["source_tool"] == "build_tool"

        dl.record_a2a_activity(
            parent_task_id=parent.task_id,
            activity={
                "agent_id": "https://ting.example/.well-known/agent-card.json",
                "skill_id": "tool-builder",
                "task_id": "task-1",
                "state": "TASK_STATE_WORKING",
                "operation": "build",
                "source_tool": "build_tool",
                "tracking_state": "poll_exhausted",
            },
        )

        assert dl.resident_hud_status()["a2a_tasks"] == []

    @pytest.mark.asyncio
    async def test_no_cascade_skuld_and_mesh_uses_composite(self):
        """No cascade, skuld_channel + mesh → direct Skuld stream wins for live activity."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=False)
        mock_skuld = MagicMock()
        mock_skuld.emit = AsyncMock()
        dl._skuld_channel = mock_skuld
        dl._mesh = AsyncMock()

        await dl._run_task(_make_task(output_mode=OutputMode.SURFACE))

        assert isinstance(captured[0], TaskContextChannel)
        assert captured[0]._channel is mock_skuld

    @pytest.mark.asyncio
    async def test_cascade_skuld_and_mesh_uses_composite_with_all(self):
        """cascade + skuld_channel + mesh → CompositeChannel([capture, wrapped skuld])."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=True)
        mock_skuld = MagicMock()
        mock_skuld.emit = AsyncMock()
        dl._skuld_channel = mock_skuld
        dl._mesh = AsyncMock()

        await dl._run_task(_make_task(output_mode=OutputMode.SURFACE))

        assert isinstance(captured[0], CompositeChannel)
        channel_types = [type(ch) for ch in captured[0]._channels]
        assert TaskContextChannel in channel_types
        wrapped = next(ch for ch in captured[0]._channels if isinstance(ch, TaskContextChannel))
        assert wrapped._channel is mock_skuld

    @pytest.mark.asyncio
    async def test_skuld_channel_receives_error_when_task_fails(self):
        captured: list = []

        def _agent_factory(channel, task_id, persona, triggered_by):
            captured.append(channel)
            agent = AsyncMock()
            agent.run_turn = AsyncMock(side_effect=LLMError("backend unavailable", status_code=500))
            agent.emit_session_ended = AsyncMock()
            return agent

        cfg = InitiativeConfig(
            enabled=True,
            max_concurrent_tasks=1,
            task_queue_max=10,
            queue_journal_path="/proc/no_such_dir/queue.json",
        )
        settings = MagicMock()
        settings.skuld.enabled = True
        settings.cascade.enabled = False
        settings.mesh.enabled = True
        settings.mesh.own_peer_id = "ravn-peer"
        settings.budget.daily_cap_usd = 100.0
        settings.budget.warn_at_percent = 80
        settings.budget.input_token_cost_per_million = 3.0
        settings.budget.output_token_cost_per_million = 15.0

        dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)
        mock_skuld = MagicMock()
        mock_skuld.emit = AsyncMock()
        dl._skuld_channel = mock_skuld
        dl._mesh = AsyncMock()

        await dl._run_task(_make_task("failing-task", output_mode=OutputMode.SURFACE))

        emitted = [call.args[0] for call in mock_skuld.emit.await_args_list]
        error = next(event for event in emitted if event.type == "error")
        assert error.payload["failure_kind"] == "LLMError"

    @pytest.mark.asyncio
    async def test_failed_task_does_not_publish_mesh_outcome(self):
        def _agent_factory(channel, task_id, persona, triggered_by):
            agent = AsyncMock()
            agent.run_turn = AsyncMock(side_effect=LLMError("backend unavailable", status_code=500))
            agent.emit_session_ended = AsyncMock()
            return agent

        cfg = InitiativeConfig(
            enabled=True,
            max_concurrent_tasks=1,
            task_queue_max=10,
            queue_journal_path="/proc/no_such_dir/queue.json",
        )
        settings = MagicMock()
        settings.skuld.enabled = False
        settings.cascade.enabled = True
        settings.mesh.enabled = True
        settings.mesh.own_peer_id = "ravn-peer"
        settings.budget.daily_cap_usd = 100.0
        settings.budget.warn_at_percent = 80
        settings.budget.input_token_cost_per_million = 3.0
        settings.budget.output_token_cost_per_million = 15.0

        dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)
        dl._mesh = AsyncMock()
        dl._persona_config = PersonaConfig(
            name="coder",
            produces=PersonaProduces(event_type="code.changed"),
        )

        await dl._run_task(_make_task("failed-outcome"))

        published_topics = [call.kwargs.get("topic") for call in dl._mesh.publish.await_args_list]
        assert "code.changed" not in published_topics
