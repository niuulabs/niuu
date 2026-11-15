"""The channel as the call's outlet to the phones, and which binding a broker's call gets."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from niuu.domain.message_author import MessageAuthor
from skuld.config import SkuldSettings
from skuld.voice.binding import CallOutlet
from skuld.voice.broker_room import BrokerVoiceRoom
from skuld.voice.broker_session import build_voice_channel
from skuld.voice.calls import VoiceCallBook
from skuld.voice.channel import SessionVoiceChannel
from skuld.voice.persona import VoicePromptComposer, resolve_voice_persona
from skuld.voice.protocol import ROOM_NOTE, SPEAK
from skuld.voice.room_binding import RoomVoiceBinding
from skuld.voice.room_port import RoomKind
from skuld.voice.session_binding import SessionVoiceBinding
from tests.test_skuld.test_voice_broker_session import FakeBroker
from tests.test_skuld.voice_support import (
    MODELS,
    NAME_HEADER,
    OWNER,
    USER_HEADER,
    FakeMinter,
    FakeRoom,
    make_member,
    make_post,
    persona_document,
)

OWNER_AUTHOR = MessageAuthor(user_id=OWNER, display_name="")
WAIT_SECONDS = 2.0


class SpyRoomBinding(RoomVoiceBinding):
    """The room binding, remembering the channel's calls to it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.events: list[tuple[str, MessageAuthor | None]] = []
        self.attached: list[CallOutlet] = []

    def attach(self, outlet: CallOutlet) -> None:
        self.attached.append(outlet)
        super().attach(outlet)

    async def call_started(self, author: MessageAuthor | None) -> None:
        self.events.append(("started", author))
        await super().call_started(author)

    async def call_ended(self, author: MessageAuthor | None) -> None:
        self.events.append(("ended", author))
        await super().call_ended(author)


class PhoneSocket:
    """A phone's WebSocket: frames in through a queue, frames out recorded."""

    def __init__(self) -> None:
        self.headers = {USER_HEADER: OWNER}
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.fail_sends = False
        self.closed = 0

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        raw = await self.inbox.get()
        if raw is None:
            raise WebSocketDisconnect()
        return raw

    async def send_json(self, message: Any) -> None:
        if self.fail_sends:
            raise RuntimeError("socket is gone")
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed += 1


class Phone:
    """One phone connected to the channel's WebSocket loop."""

    def __init__(self, channel: SessionVoiceChannel) -> None:
        self.socket = PhoneSocket()
        self._channel = channel
        self.task: asyncio.Task[None] | None = None

    async def connect(self) -> Phone:
        self.task = asyncio.create_task(self._channel.serve(self.socket))  # type: ignore[arg-type]
        await self._until(lambda: self._channel.health()["connections"] >= 1)
        return self

    async def _until(self, done) -> None:
        async with asyncio.timeout(WAIT_SECONDS):
            while not done():
                await asyncio.sleep(0)

    async def ask(self, message_type: str, payload: dict[str, Any] | None = None) -> dict:
        before = len(self.socket.sent)
        frame = {"type": message_type, "id": "m1", "payload": payload or {}}
        await self.socket.inbox.put(json.dumps(frame))
        await self._until(lambda: len(self.socket.sent) > before)
        return self.socket.sent[before]

    async def start_call(self, call_id: str = "c1") -> dict:
        return await self.ask("session.start", {"sessionId": call_id, "provider": "xai"})

    async def end_call(self, call_id: str = "c1") -> dict:
        return await self.ask("session.end", {"sessionId": call_id})

    async def hang_up(self) -> None:
        await self.socket.inbox.put(None)
        assert self.task is not None
        async with asyncio.timeout(WAIT_SECONDS):
            served = await self.task
        assert served is None  # the serve loop ended on the hang-up, not on an error

    def pushed(self, message_type: str) -> list[dict[str, Any]]:
        return [f for f in self.socket.sent if f["type"] == message_type]


@pytest.fixture
def rig():
    room = FakeRoom(members=(make_member("Reviewer", present=True),))
    binding = SpyRoomBinding(
        room,
        tool_timeout_seconds=60.0,
        full_read_max_chars=4096,
        card_recent_turns=6,
        card_turn_max_chars=1500,
    )
    channel = SessionVoiceChannel(
        binding=binding,
        composer=VoicePromptComposer(
            resolve_voice_persona(None, persona_document()), models=MODELS, time_zone="UTC"
        ),
        calls=VoiceCallBook(room.record),
        minter=FakeMinter(),  # type: ignore[arg-type]
        owner_id=OWNER,
        user_id_header=USER_HEADER,
        display_name_header=NAME_HEADER,
    )
    return channel, binding, room


def test_the_channel_is_the_outlet_and_hands_itself_to_its_binding(rig):
    channel, binding, room = rig

    assert isinstance(channel, CallOutlet)
    assert binding.attached == [channel]
    assert room.listener is not None


class TestPush:
    async def test_no_connections_reach_nobody(self, rig):
        channel, _, _ = rig

        assert await channel.push(ROOM_NOTE, {"text": "x"}) == 0

    async def test_a_phone_that_is_connected_but_not_on_a_call_gets_nothing(self, rig):
        channel, _, _ = rig
        phone = await Phone(channel).connect()

        assert await channel.push(ROOM_NOTE, {"text": "x"}) == 0
        assert phone.pushed(ROOM_NOTE) == []

        await phone.hang_up()

    async def test_session_start_puts_a_phone_on_the_call_and_session_end_takes_it_off(self, rig):
        channel, _, _ = rig
        phone = await Phone(channel).connect()

        await phone.start_call()
        assert await channel.push(ROOM_NOTE, {"text": "one"}) == 1
        await phone.end_call()
        assert await channel.push(ROOM_NOTE, {"text": "two"}) == 0

        assert [f["payload"]["text"] for f in phone.pushed(ROOM_NOTE)] == ["one"]
        await phone.hang_up()

    async def test_a_pushed_frame_is_a_wire_frame_with_its_own_id(self, rig):
        channel, _, _ = rig
        phone = await Phone(channel).connect()
        await phone.start_call()

        await channel.push(SPEAK, {"member": "Reviewer", "text": "Done."})
        await channel.push(SPEAK, {"member": "Reviewer", "text": "Again."})

        first, second = phone.pushed(SPEAK)
        assert first["payload"] == {"member": "Reviewer", "text": "Done."}
        assert first["id"].startswith("speak-")
        assert first["id"] != second["id"]
        await phone.hang_up()

    async def test_only_the_phones_on_a_call_are_reached_and_counted(self, rig):
        channel, _, _ = rig
        on_call = await Phone(channel).connect()
        idle = Phone(channel)
        idle.task = asyncio.create_task(channel.serve(idle.socket))  # type: ignore[arg-type]
        await idle._until(lambda: channel.health()["connections"] == 2)
        await on_call.start_call()

        reached = await channel.push(ROOM_NOTE, {"text": "x"})

        assert reached == 1
        assert len(on_call.pushed(ROOM_NOTE)) == 1
        assert idle.pushed(ROOM_NOTE) == []
        await on_call.hang_up()
        await idle.hang_up()

    async def test_a_phone_whose_socket_cannot_be_written_is_not_counted(self, rig):
        channel, _, _ = rig
        good = await Phone(channel).connect()
        await good.start_call("c1")
        broken = Phone(channel)
        broken.task = asyncio.create_task(channel.serve(broken.socket))  # type: ignore[arg-type]
        await broken._until(lambda: channel.health()["connections"] == 2)
        await broken.start_call("c2")
        broken.socket.fail_sends = True

        reached = await channel.push(ROOM_NOTE, {"text": "x"})

        assert reached == 1
        assert len(good.pushed(ROOM_NOTE)) == 1
        await good.hang_up()
        await broken.hang_up()


class TestCallPresence:
    async def test_session_start_tells_the_binding_who_called_once_per_phone(self, rig):
        channel, binding, _ = rig
        phone = await Phone(channel).connect()

        await phone.start_call("c1")
        await phone.start_call("c1")

        assert binding.events == [("started", OWNER_AUTHOR)]
        assert binding.on_call is True
        await phone.hang_up()

    async def test_session_end_tells_the_binding_the_call_is_over(self, rig):
        channel, binding, _ = rig
        phone = await Phone(channel).connect()
        await phone.start_call()

        await phone.end_call()

        assert binding.events == [("started", OWNER_AUTHOR), ("ended", OWNER_AUTHOR)]
        assert binding.on_call is False
        await phone.hang_up()
        assert binding.events[-1] == ("ended", OWNER_AUTHOR) and len(binding.events) == 2

    async def test_a_socket_that_closes_mid_call_ends_the_call(self, rig):
        channel, binding, _ = rig
        phone = await Phone(channel).connect()
        await phone.start_call()

        await phone.hang_up()

        assert binding.events == [("started", OWNER_AUTHOR), ("ended", OWNER_AUTHOR)]
        assert binding.on_call is False
        assert channel.health()["connections"] == 0
        assert await channel.push(ROOM_NOTE, {}) == 0

    async def test_a_socket_that_closes_off_a_call_ends_nothing(self, rig):
        channel, binding, _ = rig
        phone = await Phone(channel).connect()

        await phone.hang_up()

        assert binding.events == []

    async def test_one_phone_ending_a_call_it_never_started_changes_nothing(self, rig):
        channel, binding, _ = rig
        caller = await Phone(channel).connect()
        other = Phone(channel)
        other.task = asyncio.create_task(channel.serve(other.socket))  # type: ignore[arg-type]
        await other._until(lambda: channel.health()["connections"] == 2)
        await caller.start_call("c1")

        await other.end_call("c1")

        assert binding.events == [("started", OWNER_AUTHOR)]
        assert binding.on_call is True
        await caller.hang_up()
        await other.hang_up()

    async def test_a_session_end_the_record_refuses_leaves_the_phone_on_the_call(self, rig):
        channel, binding, _ = rig
        phone = await Phone(channel).connect()
        await phone.start_call("c1")

        refused = await phone.end_call("never-started")

        assert refused["type"] == "error"
        assert binding.on_call is True
        await phone.hang_up()


class TestRoomPostsReachThePhoneThroughTheChannel:
    async def test_posts_in_order_note_before_its_speak_frame_post_one_before_post_two(self, rig):
        channel, binding, room = rig
        phone = await Phone(channel).connect()
        await phone.start_call()

        room.listener(make_post("p1", text="Review.\nSpoken: Two nits."))
        room.listener(make_post("p2", text="Built.\nSpoken: Built it."))
        async with asyncio.timeout(WAIT_SECONDS):
            await asyncio.gather(*list(binding._pushes))

        frames = [
            (f["type"], f["payload"]["postId"])
            for f in phone.socket.sent
            if f["type"] in (ROOM_NOTE, SPEAK)
        ]
        assert frames == [(ROOM_NOTE, "p1"), (SPEAK, "p1"), (ROOM_NOTE, "p2"), (SPEAK, "p2")]
        await phone.hang_up()

    async def test_after_the_call_ends_posts_are_not_sent(self, rig):
        channel, binding, room = rig
        phone = await Phone(channel).connect()
        await phone.start_call()
        await phone.end_call()

        room.listener(make_post("p1"))
        await asyncio.gather(*list(binding._pushes))

        assert phone.pushed(ROOM_NOTE) == []
        await phone.hang_up()


class TestBuildVoiceChannelBinding:
    def _broker(self, tmp_path, *, room: dict[str, Any] | None = None) -> FakeBroker:
        broker = FakeBroker()
        broker._settings = SkuldSettings(
            session={
                "id": "s1",
                "name": "Release crew",
                "workspace_dir": str(tmp_path),
                "owner_id": OWNER,
            },
            volundr_api_url="http://forge.test",
            forge_mcp={"token": "forge-session-token"},
            voice={
                "enabled": True,
                "default_persona": persona_document(),
                "members": {"Reviewer": {"persona": "code-reviewer", "voices": {}}},
            },
            room=room or {},
        )
        broker._room_bridge = None
        broker.connected_people = lambda: []
        return broker

    def test_a_routed_room_broker_gets_a_room_binding_over_its_own_room(self, tmp_path):
        broker = self._broker(
            tmp_path,
            room={"enabled": True, "routed": True, "untargeted_delivery": "everyone"},
        )

        channel = build_voice_channel(broker)

        assert isinstance(channel._binding, RoomVoiceBinding)
        assert isinstance(broker._voice_room, BrokerVoiceRoom)
        assert channel._binding._room is broker._voice_room
        assert broker._voice_room.status().kind is RoomKind.TEAM
        assert channel.health()["tools"] == 4

    def test_the_binding_takes_its_bounds_from_the_voice_settings(self, tmp_path):
        broker = self._broker(tmp_path, room={"enabled": True, "routed": True})
        voice = broker._settings.voice

        binding = build_voice_channel(broker)._binding

        assert binding._full_read_max_chars == voice.full_read_max_chars
        assert binding._card_recent_turns == voice.card_recent_turns
        assert binding._card_turn_max_chars == voice.card_turn_max_chars
        assert binding._timeout_ms == int(voice.tool_timeout_seconds * 1000)

    def test_a_run_room_binding_has_no_say_to_everyone(self, tmp_path):
        broker = self._broker(tmp_path, room={"enabled": True, "routed": True})

        channel = build_voice_channel(broker)

        assert channel.health()["tools"] == 3

    @pytest.mark.parametrize(
        "room",
        [
            None,
            {"enabled": True},
            {"enabled": False, "routed": True},
        ],
    )
    def test_any_other_broker_gets_the_session_binding_and_no_voice_room(self, tmp_path, room):
        broker = self._broker(tmp_path, room=room)

        channel = build_voice_channel(broker)

        assert isinstance(channel._binding, SessionVoiceBinding)
        assert not hasattr(broker, "_voice_room")
