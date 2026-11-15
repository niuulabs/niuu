"""The broker tells a room call about new posts and questions (``Broker._voice_room`` hooks)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.cap_telemetry import reset_cap_hits
from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.conversation_models import ConversationTurn
from skuld.voice.protocol import ROOM_NOTE, SPEAK
from skuld.voice.room_binding import RoomVoiceBinding
from tests.test_skuld.test_voice_room_channel import Phone
from tests.test_skuld.voice_support import OWNER, persona_document

_SID = "5d6a1e0a-9c0a-4d5e-8a50-6a7d0bb3c9a1"


@pytest.fixture(autouse=True)
def _fresh_counts():
    reset_cap_hits()
    yield
    reset_cap_hits()


def _broker(tmp_path, *, routed: bool = False) -> Broker:
    settings = SkuldSettings(
        session={
            "id": _SID,
            "name": "Release crew",
            "workspace_dir": str(tmp_path),
            "owner_id": OWNER,
        },
        volundr_api_url="http://forge.test",
        forge_mcp={"token": "forge-session-token"},
        voice_channel={"enabled": routed, "default_persona": persona_document()},
        room={"enabled": routed, "routed": routed, "untargeted_delivery": "everyone"},
        transport="subprocess",
        peer_inbox={"enabled": False},
    )
    broker = Broker(settings=settings)
    broker._transport = MagicMock()
    broker._transport.is_alive = True
    broker._transport.capabilities = TransportCapabilities()
    return broker


def _turn(turn_id: str = "t1", content: str = "Done.", **fields) -> ConversationTurn:
    return ConversationTurn(
        id=turn_id,
        role="assistant",
        content=content,
        participant_id="peer-r",
        participant_meta={"display_name": "Reviewer", "persona": "code-reviewer"},
        **fields,
    )


def test_a_broker_has_no_voice_room_until_a_call_is_bound_to_it(tmp_path):
    assert _broker(tmp_path)._voice_room is None


class TestAppendTurn:
    def test_the_turn_is_passed_to_the_voice_room_once_it_is_kept(self, tmp_path):
        broker = _broker(tmp_path)
        broker._voice_room = MagicMock()
        turn = _turn()

        broker._append_turn(turn)

        broker._voice_room.observe_turn.assert_called_once_with(turn)
        assert broker._conversation_turns == [turn]

    def test_the_turn_is_durable_before_the_room_call_hears_it(self, tmp_path):
        broker = _broker(tmp_path)
        seen: list[bool] = []
        room = MagicMock()
        room.observe_turn.side_effect = lambda turn: seen.append(
            broker._conversation_history_path().exists()
        )
        broker._voice_room = room

        broker._append_turn(_turn())

        assert seen == [True]

    def test_without_a_voice_room_the_turn_is_kept_and_nothing_else_happens(self, tmp_path):
        broker = _broker(tmp_path)
        turn = _turn()

        broker._append_turn(turn)

        assert broker._conversation_turns == [turn]

    def test_a_voice_room_that_raises_is_not_hidden(self, tmp_path):
        broker = _broker(tmp_path)
        broker._voice_room = MagicMock()
        broker._voice_room.observe_turn.side_effect = RuntimeError("listener bug")

        with pytest.raises(RuntimeError, match="listener bug"):
            broker._append_turn(_turn())


class TestHelpNeeded:
    def _arm(self, broker: Broker, *, request: object | None = SimpleNamespace(id="req-1")) -> None:
        broker._room_bridge = SimpleNamespace(participants={})
        broker._observer_peer_id = lambda: "observer"
        broker._record_pending_help_request = MagicMock(return_value=request)
        broker._report_peer_help_needed_activity = AsyncMock()

    async def test_a_question_an_agent_asks_is_passed_to_the_voice_room(self, tmp_path):
        broker = _broker(tmp_path)
        self._arm(broker)
        broker._voice_room = MagicMock()
        frame = {"id": "help-1", "data": "Which database?"}

        await broker._observe_room_peer_event("peer-r", "help_needed", frame)

        broker._voice_room.observe_question.assert_called_once_with("peer-r", frame)
        broker._report_peer_help_needed_activity.assert_awaited_once()

    async def test_a_help_request_the_broker_does_not_record_is_not_passed_on(self, tmp_path):
        broker = _broker(tmp_path)
        self._arm(broker, request=None)
        broker._voice_room = MagicMock()

        await broker._observe_room_peer_event("peer-r", "help_needed", {"data": "Which db?"})

        broker._voice_room.observe_question.assert_not_called()
        broker._report_peer_help_needed_activity.assert_not_awaited()

    async def test_other_events_are_not_questions(self, tmp_path):
        broker = _broker(tmp_path)
        self._arm(broker)
        broker._voice_room = MagicMock()

        await broker._observe_room_peer_event("peer-r", "heartbeat", {"data": "x"})

        broker._voice_room.observe_question.assert_not_called()

    async def test_without_a_voice_room_the_question_is_recorded_as_before(self, tmp_path):
        broker = _broker(tmp_path)
        self._arm(broker)

        await broker._observe_room_peer_event("peer-r", "help_needed", {"data": "Which db?"})

        broker._report_peer_help_needed_activity.assert_awaited_once()


class TestOnARoutedBroker:
    async def test_a_post_and_a_question_reach_a_phone_on_the_call_through_the_broker(
        self, tmp_path
    ):
        broker = _broker(tmp_path, routed=True)
        broker._start_voice_channel()
        channel = broker._voice_channel
        binding = channel._binding
        assert isinstance(binding, RoomVoiceBinding)
        assert broker._voice_room is not None
        broker._room_bridge = SimpleNamespace(participants={})
        broker._observer_peer_id = lambda: "observer"
        broker._record_pending_help_request = MagicMock(return_value=SimpleNamespace(id="r"))
        broker._report_peer_help_needed_activity = AsyncMock()
        phone = await Phone(channel).connect()
        await phone.start_call()

        broker._append_turn(_turn("t1", "Full review.\nSpoken: Two nits."))
        await broker._observe_room_peer_event(
            "peer-r", "help_needed", {"id": "help-1", "data": "Which database?"}
        )
        async with asyncio.timeout(2):
            await asyncio.gather(*list(binding._pushes))

        frames = [
            (f["type"], f["payload"]["postId"], f["payload"].get("speak"))
            for f in phone.socket.sent
            if f["type"] in (ROOM_NOTE, SPEAK)
        ]
        assert frames == [
            (ROOM_NOTE, "t1", False),
            (SPEAK, "t1", None),
            (ROOM_NOTE, "help-1", True),
        ]
        await phone.hang_up()
