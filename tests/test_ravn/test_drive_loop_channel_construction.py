"""Tests for DriveLoop channel construction with mesh (NIU-634).

Verifies that MeshActivityChannel is included in the composite channel
when mesh is enabled, and that the channel construction falls back
correctly when mesh is not available.
"""

from __future__ import annotations

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


def _make_task(task_id: str = "t1") -> AgentTask:
    return AgentTask(
        task_id=task_id,
        title="test",
        initiative_context="do it",
        triggered_by="test",
        output_mode=OutputMode.SILENT,
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
    settings.budget.daily_cap_usd = 100.0
    settings.budget.warn_at_percent = 80
    settings.budget.input_token_cost_per_million = 3.0
    settings.budget.output_token_cost_per_million = 15.0

    dl = DriveLoop(agent_factory=_agent_factory, config=cfg, settings=settings)
    return dl, captured


class TestDriveLoopChannelConstruction:
    """Verify channel composition logic in _run_task."""

    @pytest.mark.asyncio
    async def test_mesh_enabled_no_cascade_uses_mesh_channel(self):
        """Mesh enabled, no direct Skuld stream → channel wraps MeshActivityChannel."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=False)
        mock_mesh = AsyncMock()
        dl._mesh = mock_mesh

        await dl._run_task(_make_task())

        assert len(captured) == 1
        channel = captured[0]
        assert isinstance(channel, TaskContextChannel)
        assert isinstance(channel._channel, MeshActivityChannel)

    @pytest.mark.asyncio
    async def test_mesh_disabled_no_cascade_uses_silent(self):
        """Mesh disabled, no cascade → SilentChannel."""
        dl, captured = _make_drive_loop_with_mesh(cascade_enabled=False, mesh_enabled=False)
        dl._mesh = None

        await dl._run_task(_make_task())

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

        await dl._run_task(_make_task())

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

        await dl._run_task(_make_task())

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

        await dl._run_task(_make_task())

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

        await dl._run_task(_make_task("failing-task"))

        emitted = [call.args[0] for call in mock_skuld.emit.await_args_list]
        assert any(event.type == "error" for event in emitted)

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
