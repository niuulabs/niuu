"""Tests for SessionJoinManager and the session_join tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.tools.session_join_tool import SessionJoinTool
from ravn.config import Settings
from ravn.session_join import (
    SessionJoinManager,
    build_session_join_manager,
    derive_ravn_ws_url,
)


class TestBuildManager:
    def test_defaults_to_ravn_daemon_peer(self):
        m = build_session_join_manager(Settings())
        assert m.peer_id == "ravn-daemon"

    def test_uses_mesh_peer_id_when_enabled(self):
        s = Settings()
        s.mesh.enabled = True
        s.mesh.own_peer_id = "flock-product-steward"
        m = build_session_join_manager(s)
        assert m.peer_id == "flock-product-steward"


class TestDeriveRavnWsUrl:
    def test_session_suffix_replaced(self):
        assert (
            derive_ravn_ws_url("ws://host:8080/s/abc/session", "steward")
            == "ws://host:8080/s/abc/ws/ravn/steward"
        )

    def test_http_scheme_upgraded(self):
        assert derive_ravn_ws_url("https://host/s/abc/session", "p").startswith("wss://")

    def test_missing_endpoint_fails(self):
        with pytest.raises(ValueError, match="chat_endpoint"):
            derive_ravn_ws_url("", "p")

    def test_missing_peer_fails(self):
        with pytest.raises(ValueError, match="peer_id"):
            derive_ravn_ws_url("ws://host/s/abc/session", " ")


def _manager() -> SessionJoinManager:
    return SessionJoinManager(peer_id="flock-product-steward", persona="product-steward")


def _connected_channel() -> MagicMock:
    channel = MagicMock()
    channel.connect = AsyncMock()
    channel.disconnect = AsyncMock()
    channel.emit = AsyncMock()
    channel.connected = True
    return channel


class TestSessionJoinManager:
    async def test_join_creates_connected_channel(self):
        manager = _manager()
        channel = _connected_channel()
        with patch("ravn.session_join.SkuldChannel", return_value=channel) as ctor:
            info = await manager.join("sess-1", "ws://host/s/sess-1/session")
        assert info["session_id"] == "sess-1"
        assert info["connected"] is True
        assert ctor.call_args.kwargs["broker_url"].endswith("/ws/ravn/flock-product-steward")
        channel.connect.assert_awaited_once()

    async def test_join_failure_raises(self):
        manager = _manager()
        channel = _connected_channel()
        channel.connected = False
        with (
            patch("ravn.session_join.SkuldChannel", return_value=channel),
            pytest.raises(RuntimeError, match="failed to join"),
        ):
            await manager.join("sess-1", "ws://host/s/sess-1/session")

    async def test_join_idempotent(self):
        manager = _manager()
        channel = _connected_channel()
        with patch("ravn.session_join.SkuldChannel", return_value=channel):
            await manager.join("sess-1", "ws://host/s/sess-1/session")
            await manager.join("sess-1", "ws://host/s/sess-1/session")
        channel.connect.assert_awaited_once()
        assert len(manager.joined()) == 1

    async def test_observer_receives_tagged_messages(self):
        manager = _manager()
        received: list[tuple[str, str, dict | None]] = []

        async def observer(session_id: str, content: str, metadata: dict | None) -> None:
            received.append((session_id, content, metadata))

        manager.set_observer(observer)
        channel = _connected_channel()
        with patch("ravn.session_join.SkuldChannel", return_value=channel):
            await manager.join("sess-1", "ws://host/s/sess-1/session")
        handler = channel.on_directed_message.call_args.args[0]
        await handler("need input", {"k": "v"})
        assert received == [("sess-1", "need input", {"k": "v"})]

    async def test_leave(self):
        manager = _manager()
        channel = _connected_channel()
        with patch("ravn.session_join.SkuldChannel", return_value=channel):
            await manager.join("sess-1", "ws://host/s/sess-1/session")
        assert await manager.leave("sess-1") is True
        channel.disconnect.assert_awaited_once()
        assert manager.joined() == []

    async def test_leave_unknown_returns_false(self):
        assert await _manager().leave("nope") is False

    async def test_post_into_joined_room(self):
        manager = _manager()
        channel = _connected_channel()
        with patch("ravn.session_join.SkuldChannel", return_value=channel):
            await manager.join("sess-1", "ws://host/s/sess-1/session")
        assert await manager.post("sess-1", "answer: yes") is True
        event = channel.emit.await_args.args[0]
        assert event.payload["text"] == "answer: yes"

    async def test_post_unjoined_returns_false(self):
        assert await _manager().post("nope", "hi") is False

    async def test_close_leaves_all(self):
        manager = _manager()
        channel = _connected_channel()
        with patch("ravn.session_join.SkuldChannel", return_value=channel):
            await manager.join("a", "ws://h/s/a/session")
            await manager.join("b", "ws://h/s/b/session")
        await manager.close()
        assert manager.joined() == []


class TestSessionJoinTool:
    async def test_join_action(self):
        manager = MagicMock()
        manager.join = AsyncMock(return_value={"session_id": "s1", "connected": True})
        tool = SessionJoinTool(manager)
        result = await tool.execute(
            {"action": "join", "session_id": "s1", "chat_endpoint": "ws://h/s/s1/session"}
        )
        assert not result.is_error
        assert json.loads(result.content)["session_id"] == "s1"

    async def test_join_requires_endpoint(self):
        tool = SessionJoinTool(MagicMock())
        result = await tool.execute({"action": "join", "session_id": "s1"})
        assert result.is_error

    async def test_join_failure_becomes_tool_error(self):
        manager = MagicMock()
        manager.join = AsyncMock(side_effect=RuntimeError("broker unreachable"))
        tool = SessionJoinTool(manager)
        result = await tool.execute(
            {"action": "join", "session_id": "s1", "chat_endpoint": "ws://h/s/s1/session"}
        )
        assert result.is_error
        assert "unreachable" in result.content

    async def test_leave_action(self):
        manager = MagicMock()
        manager.leave = AsyncMock(return_value=True)
        tool = SessionJoinTool(manager)
        result = await tool.execute({"action": "leave", "session_id": "s1"})
        assert not result.is_error

    async def test_leave_not_joined(self):
        manager = MagicMock()
        manager.leave = AsyncMock(return_value=False)
        tool = SessionJoinTool(manager)
        result = await tool.execute({"action": "leave", "session_id": "s1"})
        assert result.is_error

    async def test_list_action(self):
        manager = MagicMock()
        manager.joined.return_value = [{"session_id": "s1"}]
        tool = SessionJoinTool(manager)
        result = await tool.execute({"action": "list"})
        assert json.loads(result.content) == [{"session_id": "s1"}]

    async def test_post_action(self):
        manager = MagicMock()
        manager.post = AsyncMock(return_value=True)
        tool = SessionJoinTool(manager)
        result = await tool.execute({"action": "post", "session_id": "s1", "text": "hello"})
        assert not result.is_error

    async def test_post_unjoined(self):
        manager = MagicMock()
        manager.post = AsyncMock(return_value=False)
        tool = SessionJoinTool(manager)
        result = await tool.execute({"action": "post", "session_id": "s1", "text": "hello"})
        assert result.is_error

    async def test_unknown_action(self):
        tool = SessionJoinTool(MagicMock())
        result = await tool.execute({"action": "dance"})
        assert result.is_error
