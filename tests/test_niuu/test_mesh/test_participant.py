"""Tests for niuu.mesh.participant.MeshParticipant."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.mesh.participant import MeshParticipant


@pytest.fixture
def mock_mesh():
    mesh = AsyncMock()
    mesh.start = AsyncMock()
    mesh.stop = AsyncMock()
    mesh.publish = AsyncMock()
    mesh.subscribe = AsyncMock()
    mesh.unsubscribe = AsyncMock()
    mesh.set_rpc_handler = MagicMock()
    return mesh


@pytest.fixture
def mock_discovery():
    discovery = AsyncMock()
    discovery.start = AsyncMock()
    discovery.stop = AsyncMock()
    return discovery


@pytest.fixture
def participant(mock_mesh):
    return MeshParticipant(mesh=mock_mesh, peer_id="test-peer")


class TestMeshParticipantLifecycle:
    @pytest.mark.asyncio
    async def test_start_starts_mesh(self, participant, mock_mesh):
        await participant.start()
        mock_mesh.start.assert_awaited_once()
        assert participant.is_running is True

    @pytest.mark.asyncio
    async def test_start_starts_discovery_before_mesh(self, mock_mesh, mock_discovery):
        call_order: list[str] = []
        mock_discovery.start = AsyncMock(side_effect=lambda: call_order.append("discovery"))
        mock_mesh.start = AsyncMock(side_effect=lambda: call_order.append("mesh"))

        p = MeshParticipant(mesh=mock_mesh, discovery=mock_discovery, peer_id="p")
        await p.start()

        assert call_order == ["discovery", "mesh"]

    @pytest.mark.asyncio
    async def test_stop_stops_mesh_then_discovery(self, mock_mesh, mock_discovery):
        call_order: list[str] = []
        mock_mesh.stop = AsyncMock(side_effect=lambda: call_order.append("mesh"))
        mock_discovery.stop = AsyncMock(side_effect=lambda: call_order.append("discovery"))

        p = MeshParticipant(mesh=mock_mesh, discovery=mock_discovery, peer_id="p")
        await p.start()
        await p.stop()

        assert call_order == ["mesh", "discovery"]
        assert p.is_running is False

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self, participant, mock_mesh):
        await participant.start()
        await participant.start()
        mock_mesh.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_noop(self, participant, mock_mesh):
        await participant.stop()
        mock_mesh.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_mesh_start_does_not_raise(self):
        p = MeshParticipant(mesh=None)
        await p.start()
        assert p.is_running is True

    @pytest.mark.asyncio
    async def test_no_mesh_stop_does_not_raise(self):
        p = MeshParticipant(mesh=None)
        await p.start()
        await p.stop()
        assert p.is_running is False

    @pytest.mark.asyncio
    async def test_mesh_start_failure_sets_running(self, mock_mesh):
        mock_mesh.start = AsyncMock(side_effect=RuntimeError("mesh error"))
        p = MeshParticipant(mesh=mock_mesh, peer_id="p")
        await p.start()
        # Even with mesh failure, participant transitions to running
        assert p.is_running is True

    def test_peer_id_property(self):
        p = MeshParticipant(mesh=None, peer_id="my-peer")
        assert p.peer_id == "my-peer"

    def test_mesh_property(self, mock_mesh):
        p = MeshParticipant(mesh=mock_mesh, peer_id="p")
        assert p.mesh is mock_mesh

    def test_discovery_property(self, mock_mesh, mock_discovery):
        p = MeshParticipant(mesh=mock_mesh, discovery=mock_discovery)
        assert p.discovery is mock_discovery


class TestMeshParticipantOperations:
    @pytest.mark.asyncio
    async def test_publish_delegates_to_mesh(self, participant, mock_mesh):
        event = MagicMock()
        await participant.publish(event, "test.topic")
        mock_mesh.publish.assert_awaited_once_with(event, topic="test.topic")

    @pytest.mark.asyncio
    async def test_publish_noop_when_no_mesh(self):
        p = MeshParticipant(mesh=None)
        await p.publish(MagicMock(), "topic")  # Should not raise

    @pytest.mark.asyncio
    async def test_subscribe_delegates_to_mesh(self, participant, mock_mesh):
        handler = AsyncMock()
        await participant.subscribe("my.topic", handler)
        mock_mesh.subscribe.assert_awaited_once_with("my.topic", handler)

    @pytest.mark.asyncio
    async def test_subscribe_noop_when_no_mesh(self):
        p = MeshParticipant(mesh=None)
        await p.subscribe("topic", AsyncMock())  # Should not raise

    @pytest.mark.asyncio
    async def test_unsubscribe_delegates_to_mesh(self, participant, mock_mesh):
        await participant.unsubscribe("my.topic")
        mock_mesh.unsubscribe.assert_awaited_once_with("my.topic")

    @pytest.mark.asyncio
    async def test_unsubscribe_noop_when_no_mesh(self):
        p = MeshParticipant(mesh=None)
        await p.unsubscribe("topic")  # Should not raise

    def test_set_rpc_handler_delegates_to_mesh(self, participant, mock_mesh):
        handler = MagicMock()
        participant.set_rpc_handler(handler)
        mock_mesh.set_rpc_handler.assert_called_once_with(handler)

    def test_set_rpc_handler_noop_when_no_mesh(self):
        p = MeshParticipant(mesh=None)
        p.set_rpc_handler(MagicMock())  # Should not raise


class TestMeshParticipantIntegration:
    """Integration tests using InProcessBus."""

    @pytest.mark.asyncio
    async def test_publish_subscribe_round_trip(self):
        from niuu.mesh import build_in_process_mesh

        mesh = build_in_process_mesh("int-peer", rpc_timeout_s=5.0)
        p = MeshParticipant(mesh=mesh, peer_id="int-peer")

        from datetime import UTC, datetime

        from ravn.domain.events import RavnEvent, RavnEventType

        received: list[RavnEvent] = []

        async def handler(event: RavnEvent) -> None:
            received.append(event)

        await p.start()
        await p.subscribe("test.topic", handler)

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="test-src",
            payload={"data": "hello"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="c1",
            session_id="s1",
        )
        await p.publish(event, "test.topic")

        await mesh._publisher.flush()

        import asyncio

        await asyncio.sleep(0.05)

        assert len(received) >= 1

        await p.stop()
