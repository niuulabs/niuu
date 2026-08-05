"""Tests for MeshActivityChannel (NIU-634)."""

from __future__ import annotations

from asyncio import sleep
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.channels.mesh_channel import MeshActivityChannel
from ravn.domain.events import RavnEvent, RavnEventType


def _make_event(event_type: RavnEventType = RavnEventType.THOUGHT) -> RavnEvent:
    return RavnEvent(
        type=event_type,
        source="ravn-test",
        payload={"text": "hello"},
        timestamp=datetime.now(UTC),
        urgency=0.3,
        correlation_id="c1",
        session_id="s1",
    )


def _make_mesh() -> MagicMock:
    mesh = MagicMock()
    mesh.publish = AsyncMock()
    return mesh


class TestMeshActivityChannel:
    def test_topic_uses_peer_id(self):
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "my-peer")
        assert ch._topic == "activity.my-peer"

    @pytest.mark.asyncio
    async def test_emit_publishes_to_mesh(self):
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "peer-01")
        event = _make_event(RavnEventType.RESPONSE)
        await ch.emit(event)
        await sleep(0)
        mesh.publish.assert_awaited_once_with(event, topic="activity.peer-01")

    @pytest.mark.asyncio
    async def test_emit_drops_ephemeral_thought_event(self):
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "p1")
        event = _make_event(RavnEventType.THOUGHT)
        await ch.emit(event)
        mesh.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_emit_publishes_usage_so_a_mesh_only_peer_reports_its_cost(self):
        """A flock persona has no WebSocket to Skuld — the mesh is its only route.

        USAGE is emitted once per completed turn, not per token, and Skuld's
        room adapter already consumes ``kind: "usage"`` and dedupes on
        ``usage_id``. Dropping it here as "ephemeral" was what left every flock
        session showing zero tokens and zero cost.
        """
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "flock-research-framer")

        await ch.emit(_make_event(RavnEventType.USAGE))
        await sleep(0)

        mesh.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_response_event(self):
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "p1")
        event = _make_event(RavnEventType.RESPONSE)
        await ch.emit(event)
        await sleep(0)
        mesh.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_tool_start_event(self):
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "p1")
        event = _make_event(RavnEventType.TOOL_START)
        await ch.emit(event)
        await sleep(0)
        mesh.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_suppresses_publish_exception(self):
        """Publish failures must not propagate to callers."""
        mesh = _make_mesh()
        mesh.publish.side_effect = RuntimeError("mesh down")
        ch = MeshActivityChannel(mesh, "p1")
        # Must not raise
        await ch.emit(_make_event(RavnEventType.ERROR))
        await sleep(0)

    @pytest.mark.asyncio
    async def test_emit_logs_warning_on_exception(self):
        mesh = _make_mesh()
        mesh.publish.side_effect = RuntimeError("mesh down")
        ch = MeshActivityChannel(mesh, "p1")
        with patch("ravn.adapters.channels.mesh_channel.logger") as mock_logger:
            await ch.emit(_make_event(RavnEventType.ERROR))
            await sleep(0)
        mock_logger.warning.assert_called_once()
