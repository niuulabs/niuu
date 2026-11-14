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


def _everyone_broker(*, host_peer_id: str = "team-root") -> Broker:
    settings = SkuldSettings(
        session={"id": "team-root"},
        transport="subprocess",
        room={
            "enabled": True,
            "environment_id": "team-1",
            "routed": True,
            "realm_room": True,
            "untargeted_delivery": "everyone",
        },
    )
    broker = Broker(settings=settings)
    broker._room_bridge = MagicMock()
    broker._room_bridge.participants = {}
    broker._transport = MagicMock(is_alive=False)
    broker._mesh_adapter = SimpleNamespace(peer_id=host_peer_id)
    broker._reply_context_consumption_allowed = MagicMock(return_value=True)
    return broker


class TestUntargetedDeliveryConfig:
    def test_refuse_is_the_default(self):
        assert SkuldSettings().room.untargeted_delivery == "refuse"

    def test_an_unknown_value_is_refused_at_startup(self):
        with pytest.raises(ValueError, match="untargeted_delivery"):
            SkuldSettings(room={"untargeted_delivery": "broadcast"})

    def test_everyone_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("SKULD__ROOM__UNTARGETED_DELIVERY", "everyone")
        assert SkuldSettings().room.untargeted_delivery == "everyone"


class TestRoomAgentPeerIds:
    def test_harness_members_count_but_the_host_people_and_workflows_do_not(self):
        broker = _everyone_broker()
        _with_participants(
            broker,
            _participant("team-root", participant_type="skuld"),
            _participant("member-a", participant_type="skuld"),
            _participant("member-b", participant_type="ravn"),
            _participant("human:ada", participant_type="human"),
            _participant("run-1", participant_type="workflow"),
        )
        assert broker._room_agent_peer_ids() == ["member-a", "member-b"]

    def test_a_room_without_a_mesh_adapter_excludes_nobody_by_id(self):
        broker = _everyone_broker()
        broker._mesh_adapter = None
        _with_participants(broker, _participant("member-a", participant_type="skuld"))
        assert broker._room_agent_peer_ids() == ["member-a"]

    def test_no_room_bridge_means_no_agents(self):
        broker = _everyone_broker()
        broker._room_bridge = None
        assert broker._room_agent_peer_ids() == []


class TestUntargetedEveryone:
    @pytest.mark.asyncio
    async def test_two_agents_each_get_a_directed_message(self):
        broker = _everyone_broker()
        _with_participants(
            broker,
            _participant("team-root", participant_type="skuld"),
            _participant("member-a", participant_type="skuld"),
            _participant("member-b", participant_type="skuld"),
            _participant("human:ada", participant_type="human"),
        )
        broker.handle_directed_room_message = AsyncMock(return_value="msg")

        await broker._dispatch_browser_message({"content": "hello all", "request_id": "r-1"})

        calls = broker.handle_directed_room_message.await_args_list
        assert [call.args[0] for call in calls] == ["member-a", "member-b"]
        assert all(call.args[1] == "hello all" for call in calls)
        # The client's one optimistic message is confirmed once, by the first delivery.
        assert [call.kwargs["request_id"] for call in calls] == ["r-1", None]

    @pytest.mark.asyncio
    async def test_one_agent_gets_the_message(self):
        broker = _everyone_broker()
        _with_participants(broker, _participant("member-a", participant_type="skuld"))
        broker.handle_directed_room_message = AsyncMock(return_value="msg")

        await broker._dispatch_browser_message({"content": "hello"})

        broker.handle_directed_room_message.assert_awaited_once()
        assert broker.handle_directed_room_message.await_args.args[0] == "member-a"

    @pytest.mark.asyncio
    async def test_no_agent_is_an_error_to_the_sender(self):
        broker = _everyone_broker()
        _with_participants(
            broker,
            _participant("team-root", participant_type="skuld"),
            _participant("human:ada", participant_type="human"),
        )
        broker.handle_directed_room_message = AsyncMock()
        broker._send_broker_frame_to = AsyncMock()

        await broker._dispatch_browser_message({"content": "hello"}, sender_ws=MagicMock())

        broker.handle_directed_room_message.assert_not_awaited()
        frame = broker._send_broker_frame_to.await_args[0][1]
        assert frame["type"] == "error"
        assert "No agent in this room" in frame["content"]

    @pytest.mark.asyncio
    async def test_a_failed_delivery_does_not_stop_the_others_and_is_raised(self):
        broker = _everyone_broker()
        _with_participants(
            broker,
            _participant("member-a", participant_type="skuld"),
            _participant("member-b", participant_type="skuld"),
        )
        broker.handle_directed_room_message = AsyncMock(
            side_effect=[RuntimeError("mesh refused member-a"), "msg"]
        )

        with pytest.raises(RuntimeError, match="mesh refused member-a"):
            await broker._dispatch_browser_message({"content": "hello"})

        assert broker.handle_directed_room_message.await_count == 2

    @pytest.mark.asyncio
    async def test_a_vanished_agent_is_reported_and_the_request_id_moves_on(self):
        broker = _everyone_broker()
        _with_participants(
            broker,
            _participant("member-a", participant_type="skuld"),
            _participant("member-b", participant_type="skuld"),
        )
        broker.handle_directed_room_message = AsyncMock(
            side_effect=[LookupError("Unknown room participant: member-a"), "msg"]
        )
        broker._send_broker_frame_to = AsyncMock()

        await broker._dispatch_browser_message(
            {"content": "hello", "request_id": "r-1"}, sender_ws=MagicMock()
        )

        calls = broker.handle_directed_room_message.await_args_list
        assert [call.kwargs["request_id"] for call in calls] == ["r-1", "r-1"]
        assert broker._send_broker_frame_to.await_args[0][1]["type"] == "error"

    @pytest.mark.asyncio
    async def test_an_agent_awaiting_an_operator_reply_is_skipped_for_a_non_approver(self):
        broker = _everyone_broker()
        _with_participants(
            broker,
            _participant("member-a", participant_type="skuld"),
            _participant("member-b", participant_type="skuld"),
        )
        broker._reply_context_consumption_allowed = MagicMock(
            side_effect=lambda target, role: target != "member-a"
        )
        broker.handle_directed_room_message = AsyncMock(return_value="msg")
        broker._send_broker_frame_to = AsyncMock()

        await broker._dispatch_browser_message({"content": "hello"}, sender_ws=MagicMock())

        broker.handle_directed_room_message.assert_awaited_once()
        assert broker.handle_directed_room_message.await_args.args[0] == "member-b"
        assert broker._send_broker_frame_to.await_args[0][1]["code"] == "forbidden"

    @pytest.mark.asyncio
    async def test_refuse_keeps_two_agents_ambiguous_exactly_as_before(self):
        broker = _everyone_broker()
        broker._settings.room.untargeted_delivery = "refuse"
        _with_participants(
            broker,
            _participant("member-a", participant_type="ravn"),
            _participant("member-b", participant_type="ravn"),
        )
        broker.handle_directed_room_message = AsyncMock()
        broker._send_broker_frame_to = AsyncMock()

        await broker._dispatch_browser_message({"content": "hello"}, sender_ws=MagicMock())

        broker.handle_directed_room_message.assert_not_awaited()
        frame = broker._send_broker_frame_to.await_args[0][1]
        assert "No room participant to deliver this message to" in frame["content"]

    @pytest.mark.asyncio
    async def test_a_configured_default_target_still_wins_over_everyone(self):
        broker = _everyone_broker()
        broker._settings.room.default_target_peer_id = "chosen"
        _with_participants(
            broker,
            _participant("member-a", participant_type="skuld"),
            _participant("member-b", participant_type="skuld"),
        )
        broker.handle_directed_room_message = AsyncMock(return_value="msg")

        await broker._dispatch_browser_message({"content": "hello"})

        broker.handle_directed_room_message.assert_awaited_once()
        assert broker.handle_directed_room_message.await_args.args[0] == "chosen"


class TestRealmRoomSeat:
    def test_a_peer_of_the_rooms_own_realm_is_seated_in_the_room(self):
        assert _everyone_broker()._realm_room_seat("team-1") == ("team-1",)

    def test_a_peer_of_another_realm_is_not_seated(self):
        assert _everyone_broker()._realm_room_seat("other") == ()

    def test_a_room_that_is_not_a_realm_room_seats_nobody(self):
        broker = _everyone_broker()
        broker._settings.room.realm_room = False
        assert broker._realm_room_seat("team-1") == ()
