"""Tests for resident session routing (room.default_target_peer_id).

Resident sessions run one long-lived ravn behind the room. Untargeted browser
messages must route to it as directed messages, and the broker must never
lazy-start its own CLI transport.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.broker import Broker
from skuld.config import SkuldSettings


def _resident_broker(default_target: str = "flock-product-steward") -> Broker:
    settings = SkuldSettings(
        session={"id": "resident-session"},
        transport="subprocess",
        room={"enabled": True, "default_target_peer_id": default_target},
    )
    broker = Broker(settings=settings)
    broker._room_bridge = MagicMock()
    broker._transport = MagicMock(is_alive=False)
    return broker


class TestRoomDefaultTarget:
    def test_default_target_requires_room_bridge(self):
        broker = _resident_broker()
        broker._room_bridge = None
        assert broker._room_default_target_peer_id() == ""

    def test_default_target_read_from_config(self):
        broker = _resident_broker()
        assert broker._room_default_target_peer_id() == "flock-product-steward"

    def test_room_routed_session_with_default_target(self):
        broker = _resident_broker()
        assert broker._is_room_routed_session() is True

    def test_room_routed_session_without_default_target(self):
        broker = _resident_broker(default_target="")
        assert broker._is_room_routed_session() is False


class TestPlainMessageRouting:
    @pytest.mark.asyncio
    async def test_plain_content_routes_to_default_target(self):
        broker = _resident_broker()
        broker.handle_directed_room_message = AsyncMock(return_value="msg-1")
        await broker._dispatch_browser_message(
            {"content": "hello resident", "request_id": "req-resident-1"}
        )
        broker.handle_directed_room_message.assert_awaited_once()
        args, kwargs = broker.handle_directed_room_message.await_args
        assert args[0] == "flock-product-steward"
        assert args[1] == "hello resident"
        assert kwargs.get("source") == "browser"
        assert kwargs.get("request_id") == "req-resident-1"

    @pytest.mark.asyncio
    async def test_unknown_default_target_reports_error(self):
        broker = _resident_broker()
        broker.handle_directed_room_message = AsyncMock(
            side_effect=LookupError("Unknown room participant: flock-product-steward")
        )
        sender_ws = MagicMock(send_json=AsyncMock())
        broker._send_broker_frame_to = AsyncMock()
        await broker._dispatch_browser_message({"content": "hello"}, sender_ws=sender_ws)
        broker._send_broker_frame_to.assert_awaited_once()
        frame = broker._send_broker_frame_to.await_args.args[1]
        assert frame["type"] == "error"

    @pytest.mark.asyncio
    async def test_explicit_directed_message_still_works(self):
        broker = _resident_broker()
        broker.handle_directed_room_message = AsyncMock(return_value="msg-2")
        await broker._dispatch_browser_message(
            {
                "type": "directed_message",
                "targetPeerId": "other-peer",
                "content": "hi",
                "request_id": "req-directed-1",
            }
        )
        args, kwargs = broker.handle_directed_room_message.await_args
        assert args[0] == "other-peer"
        assert kwargs.get("request_id") == "req-directed-1"

    @pytest.mark.asyncio
    async def test_directed_message_uses_mesh_for_discovered_resident_peer(self):
        broker = _resident_broker()
        participant = SimpleNamespace(
            peer_id="hermes-worker",
            persona="Hermes",
            participant_kind="mesh",
        )
        broker._room_bridge.participants = {participant.peer_id: participant}
        broker._room_bridge.route_directed_message = AsyncMock(return_value=False)
        broker._room_bridge.handle_collaboration_frame = AsyncMock()
        broker.handle_human_room_message = AsyncMock(return_value="message-1")
        broker._mesh_adapter = MagicMock()
        broker._mesh_adapter.send_directed_message = AsyncMock(return_value={"status": "accepted"})

        message_id = await broker.handle_directed_room_message(
            participant.peer_id,
            "Review this",
            request_id="request-1",
        )

        assert message_id == "message-1"
        broker._mesh_adapter.send_directed_message.assert_awaited_once()
        args, kwargs = broker._mesh_adapter.send_directed_message.await_args
        assert args == (participant.peer_id, "Review this")
        assert kwargs["metadata"]["session_id"] == "resident-session"
        assert kwargs["metadata"]["root_correlation_id"] == "resident-session"
        assert "trace_context" in kwargs["metadata"]
        broker._room_bridge.handle_collaboration_frame.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_directed_message_fails_when_mesh_rejects_dispatch(self):
        broker = _resident_broker()
        participant = SimpleNamespace(
            peer_id="hermes-worker",
            persona="Hermes",
            participant_kind="mesh",
        )
        broker._room_bridge.participants = {participant.peer_id: participant}
        broker._room_bridge.route_directed_message = AsyncMock(return_value=False)
        broker.handle_human_room_message = AsyncMock(return_value="message-1")
        broker._mesh_adapter = MagicMock()
        broker._mesh_adapter.send_directed_message = AsyncMock(
            return_value={"status": "rejected", "error": "queue unavailable"}
        )

        with pytest.raises(RuntimeError, match="queue unavailable"):
            await broker.handle_directed_room_message(
                participant.peer_id,
                "Review this",
                request_id="request-1",
            )

    @pytest.mark.asyncio
    async def test_no_default_target_keeps_classic_path(self):
        broker = _resident_broker(default_target="")
        broker.handle_directed_room_message = AsyncMock()
        broker.handle_user_message = AsyncMock()
        # Without a default target and without a workflow trigger, the plain
        # message path must NOT be routed as a directed room message.
        await broker._dispatch_browser_message({"content": "hello"})
        broker.handle_directed_room_message.assert_not_awaited()
