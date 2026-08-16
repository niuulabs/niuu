"""Tests for declared room-routed brokers (``room.routed``).

A broker created by ``ravn room create`` hosts ravns and owns no CLI agent.
Two things must follow from that, and neither did before ``room.routed``
existed: the broker must never start a CLI agent of its own (it would answer
chat meant for the room's ravn), and an untargeted browser message must reach
the room's ravn rather than falling through to a transport that is not there.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.broker import Broker
from skuld.config import SkuldSettings


def _routed_broker(*, routed: bool = True, default_target: str = "") -> Broker:
    settings = SkuldSettings(
        session={"id": "desk"},
        transport="subprocess",
        room={
            "enabled": True,
            "environment_id": "desk",
            "routed": routed,
            "default_target_peer_id": default_target,
        },
    )
    broker = Broker(settings=settings)
    broker._room_bridge = MagicMock()
    broker._room_bridge.participants = {}
    broker._transport = MagicMock(is_alive=False)
    return broker


def _participant(peer_id: str, participant_type: str = "ravn") -> SimpleNamespace:
    return SimpleNamespace(peer_id=peer_id, participant_type=participant_type)


def _with_participants(broker: Broker, *participants: SimpleNamespace) -> None:
    broker._room_bridge.participants = {p.peer_id: p for p in participants}


class TestDeclaredRouted:
    def test_declared_routed_needs_a_room_bridge(self):
        broker = _routed_broker()
        broker._room_bridge = None
        assert broker._is_declared_room_routed() is False

    def test_declared_routed_marks_the_session_room_routed(self):
        # This is the guard that stops the orphan Claude subprocess.
        assert _routed_broker()._is_room_routed_session() is True

    def test_unset_routed_leaves_the_classic_path(self):
        assert _routed_broker(routed=False)._is_room_routed_session() is False


class TestSoleRavnResolution:
    def test_sole_ravn_is_unambiguous(self):
        broker = _routed_broker()
        _with_participants(broker, _participant("coder"))
        assert broker._sole_room_ravn_peer_id() == "coder"

    def test_humans_are_not_delivery_candidates(self):
        broker = _routed_broker()
        _with_participants(
            broker,
            _participant("coder"),
            _participant("human:damien", participant_type="human"),
        )
        assert broker._sole_room_ravn_peer_id() == "coder"

    def test_two_ravns_are_ambiguous(self):
        broker = _routed_broker()
        _with_participants(broker, _participant("coder"), _participant("reviewer"))
        assert broker._sole_room_ravn_peer_id() == ""

    def test_empty_room_resolves_to_nothing(self):
        assert _routed_broker()._sole_room_ravn_peer_id() == ""


class TestUntargetedDelivery:
    @pytest.mark.asyncio
    async def test_untargeted_message_reaches_the_sole_ravn(self):
        broker = _routed_broker()
        _with_participants(broker, _participant("coder"))
        broker.handle_directed_room_message = AsyncMock(return_value="msg-1")

        await broker._dispatch_browser_message({"content": "hello"})

        broker.handle_directed_room_message.assert_awaited_once()
        args, kwargs = broker.handle_directed_room_message.await_args
        assert args[0] == "coder"
        assert args[1] == "hello"
        assert kwargs["source"] == "browser"

    @pytest.mark.asyncio
    async def test_configured_default_target_wins_over_inference(self):
        broker = _routed_broker(default_target="chosen")
        _with_participants(broker, _participant("coder"))
        broker.handle_directed_room_message = AsyncMock(return_value="msg-1")

        await broker._dispatch_browser_message({"content": "hello"})

        assert broker.handle_directed_room_message.await_args[0][0] == "chosen"

    @pytest.mark.asyncio
    async def test_ambiguous_room_reports_instead_of_guessing(self):
        broker = _routed_broker()
        _with_participants(broker, _participant("coder"), _participant("reviewer"))
        broker.handle_directed_room_message = AsyncMock()
        sender = MagicMock()
        broker._send_broker_frame_to = AsyncMock()

        await broker._dispatch_browser_message({"content": "hello"}, sender_ws=sender)

        broker.handle_directed_room_message.assert_not_awaited()
        frame = broker._send_broker_frame_to.await_args[0][1]
        assert frame["type"] == "error"
        assert "directed_message" in frame["content"]

    @pytest.mark.asyncio
    async def test_empty_room_reports_instead_of_dropping(self):
        # The failure this replaces was silent: the message fell through to a
        # CLI transport that a routed broker never starts.
        broker = _routed_broker()
        broker.handle_directed_room_message = AsyncMock()
        broker._send_broker_frame_to = AsyncMock()

        await broker._dispatch_browser_message({"content": "hello"}, sender_ws=MagicMock())

        broker.handle_directed_room_message.assert_not_awaited()
        assert broker._send_broker_frame_to.await_args[0][1]["type"] == "error"
