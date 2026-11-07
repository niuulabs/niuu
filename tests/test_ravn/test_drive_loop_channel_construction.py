"""Tests for DriveLoop channel construction with mesh (NIU-634).

Verifies that MeshActivityChannel is included in the composite channel
when mesh is enabled, and that the channel construction falls back
correctly when mesh is not available.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    async def test_failure_log_names_the_exception_type(self, caplog):
        """A message-less exception must still say what it was.

        A resident home turn stalled and died logging exactly
        "drive_loop: task ... failed:" — nothing after the colon, because a
        read timeout raises with empty args and only str(exc) was logged. The
        operator got a real failure with zero diagnosis.
        """
        dl, _ = _make_drive_loop_with_mesh(cascade_enabled=False, mesh_enabled=False)
        dl._mesh = None

        def _boom(channel, task_id, persona, triggered_by):
            agent = AsyncMock()
            agent.run_turn = AsyncMock(side_effect=TimeoutError())  # no message, as httpx raises
            agent.emit_session_ended = AsyncMock()
            return agent

        dl._agent_factory = _boom

        with caplog.at_level(logging.ERROR, logger="ravn.drive_loop"):
            await dl._run_task(_make_task("stalled-task"))

        failures = [r.getMessage() for r in caplog.records if "failed" in r.getMessage()]
        assert failures, "the failure was not logged at all"
        assert "TimeoutError" in failures[0]
        assert not failures[0].rstrip().endswith("failed:")

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


class TestDriveLoopSetupFailure:
    """A task that dies before its model turn must still report a terminal failure.

    Seen live: a research coordinator whose persona needs durable workflow
    execution tools raised while its agent was being built. The exception
    escaped as "crashed before completion handling", nothing was published,
    and the workflow campaign stayed "running" for hours with nothing running.
    """

    @staticmethod
    def _failing_loop() -> DriveLoop:
        dl, _ = _make_drive_loop_with_mesh(cascade_enabled=False)

        def _agent_factory(channel, task_id, persona, triggered_by):
            raise RuntimeError(
                "Persona requires durable workflow execution tools, but an owner-bound "
                "workflow_execution runtime context is not configured"
            )

        dl._agent_factory = _agent_factory
        dl._mesh = AsyncMock()
        dl._sleipnir_publisher = AsyncMock()
        return dl

    @staticmethod
    def _coordinator_task() -> AgentTask:
        return AgentTask(
            task_id="event_research_coordinate_fe4ae2efb1c879c7",
            title="Handle mesh:outcome:research.framed",
            initiative_context="framed",
            triggered_by="mesh:outcome:research.framed",
            output_mode=OutputMode.AMBIENT,
            persona="research-coordinator",
            workflow_node_id="research-coordinate",
        )

    @pytest.mark.asyncio
    async def test_setup_failure_publishes_error_naming_the_workflow_node(self):
        dl = self._failing_loop()

        outcome = await dl._run_task(self._coordinator_task())
        await asyncio.sleep(0)  # MeshActivityChannel publishes detached
        await asyncio.sleep(0)

        assert outcome == "error"
        activity = [
            call.args[0]
            for call in dl._mesh.publish.await_args_list
            if call.kwargs.get("topic") == "activity.ravn-peer"
        ]
        error = next(event for event in activity if event.type == "error")
        assert "durable workflow execution tools" in error.payload["message"]
        assert error.payload["failure_kind"] == "RuntimeError"
        assert error.payload["workflow_node_id"] == "research-coordinate"
        assert error.payload["persona"] == "research-coordinator"
        complete = next(event for event in activity if event.type == "task_complete")
        assert complete.payload["success"] is False

    @pytest.mark.asyncio
    async def test_setup_failure_emits_task_completed_error(self):
        dl = self._failing_loop()

        await dl._run_task(self._coordinator_task())

        published = [call.args[0] for call in dl._sleipnir_publisher.publish.await_args_list]
        completed = [event for event in published if event.event_type == "ravn.task.completed"]
        assert len(completed) == 1
        assert completed[0].payload["outcome"] == "error"
        assert completed[0].payload["workflow_node_id"] == "research-coordinate"

    @pytest.mark.asyncio
    async def test_setup_failure_no_longer_escapes_as_a_crash(self, caplog):
        dl = self._failing_loop()
        task = self._coordinator_task()

        with caplog.at_level(logging.ERROR, logger="ravn.drive_loop"):
            finished = asyncio.create_task(dl._run_task(task))
            await asyncio.gather(finished, return_exceptions=True)
            dl._on_task_done(task.task_id, finished)

        assert finished.result() == "error"

        messages = [record.getMessage() for record in caplog.records]
        assert any("failed during setup: RuntimeError" in message for message in messages)
        assert not any("crashed before completion handling" in message for message in messages)
