"""Tests for MeshActivityChannel (NIU-634)."""

from __future__ import annotations

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
        event = _make_event()
        await ch.emit(event)
        mesh.publish.assert_awaited_once_with(event, topic="activity.peer-01")

    @pytest.mark.asyncio
    async def test_emit_thought_event(self):
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "p1")
        event = _make_event(RavnEventType.THOUGHT)
        await ch.emit(event)
        call_args = mesh.publish.call_args
        assert call_args[0][0].type == RavnEventType.THOUGHT
        assert call_args[1]["topic"] == "activity.p1"

    @pytest.mark.asyncio
    async def test_emit_response_event(self):
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "p1")
        event = _make_event(RavnEventType.RESPONSE)
        await ch.emit(event)
        mesh.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_tool_start_event(self):
        mesh = _make_mesh()
        ch = MeshActivityChannel(mesh, "p1")
        event = _make_event(RavnEventType.TOOL_START)
        await ch.emit(event)
        mesh.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emit_suppresses_publish_exception(self):
        """Publish failures must not propagate to callers."""
        mesh = _make_mesh()
        mesh.publish.side_effect = RuntimeError("mesh down")
        ch = MeshActivityChannel(mesh, "p1")
        # Must not raise
        await ch.emit(_make_event())

    @pytest.mark.asyncio
    async def test_emit_logs_warning_on_exception(self):
        mesh = _make_mesh()
        mesh.publish.side_effect = RuntimeError("mesh down")
        ch = MeshActivityChannel(mesh, "p1")
        with patch("ravn.adapters.channels.mesh_channel.logger") as mock_logger:
            await ch.emit(_make_event())
        mock_logger.warning.assert_called_once()
