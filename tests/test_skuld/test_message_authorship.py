"""Every human message carries its author, and the model hears who said it.

The session proxy stamps the verified identity on each connection (user id and display
name); the broker records it on the turn, the durable user frame, the ``user_confirmed``
broadcast, the history rows and the trace span. Once more than one person has spoken in a
session, every human message the model reads opens with ``[<name>] ``. Turns recorded
before authorship existed carry no author and show none.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.message_author import MessageAuthor
from niuu.domain.transcript_reducer import reduce_frames
from skuld.broker import Broker
from skuld.channels import WebSocketChannel
from skuld.config import SkuldSettings
from skuld.conversation_models import ConversationTurn
from skuld.conversation_read import conversation_rows
from skuld.transports import TransportCapabilities
from skuld.websocket_auth import author_from_headers

ALICE = MessageAuthor(user_id="u-alice", display_name="Alice Liddell")
BOB = MessageAuthor(user_id="u-bob", display_name="Bob Stone")


@pytest.fixture(autouse=True)
def delivery_claim_api(monkeypatch):
    import importlib

    module = importlib.import_module("skuld.broker")

    async def claim(*args, **kwargs):
        return {"claimed": True, "status": "pending", "request_id": kwargs["request_id"]}

    monkeypatch.setattr(module, "claim_message", claim)
    monkeypatch.setattr(module, "settle_message", AsyncMock())
    client = AsyncMock()
    client.post.return_value = MagicMock(status_code=204)
    monkeypatch.setattr(Broker, "_get_http_client", AsyncMock(return_value=client))


class _FakeWebSocket:
    """A browser socket that records what the broker sends it."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        return None


def _broker(tmp_path, *, steering_mode: str = "none", steer: bool = False) -> Broker:
    settings = SkuldSettings(
        session={"id": "test-session", "workspace_dir": str(tmp_path)},
        transport="sdk",
        volundr_api_url="http://harness.invalid",
        event_log_enabled=True,
    )
    broker = Broker(settings=settings)
    broker._transport = AsyncMock()
    broker._transport.capabilities = TransportCapabilities(steer=steer, steering_mode=steering_mode)
    broker._transport.is_turn_active = steer
    broker._apply_retrieval_reflex = AsyncMock(side_effect=lambda m: m)
    broker._save_conversation_history = MagicMock()
    return broker


def _connect(broker: Broker, author: MessageAuthor | None) -> _FakeWebSocket:
    ws = _FakeWebSocket()
    broker._channels.add(WebSocketChannel(ws, room_role="owner", author=author))
    return ws


async def _settle_delivery() -> None:
    tasks = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and t.get_name().startswith("transport-deliver-")
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _say(broker: Broker, ws: _FakeWebSocket, text: str) -> None:
    await broker._dispatch_browser_message({"content": text}, sender_ws=ws)
    await _settle_delivery()


def _frames(broker: Broker, kind: str) -> list[dict]:
    return [e["payload"] for e in broker._event_log_buffer if e["payload"].get("type") == kind]


class TestAuthorIsRecorded:
    @pytest.mark.asyncio
    async def test_the_turn_the_frame_and_the_broadcast_name_the_author(self, tmp_path):
        broker = _broker(tmp_path)
        ws = _connect(broker, ALICE)

        await _say(broker, ws, "ship it")

        (turn,) = [t for t in broker._conversation_turns if t.role == "user"]
        assert turn.author == {"user_id": "u-alice", "display_name": "Alice Liddell"}
        (user_frame,) = _frames(broker, "user")
        assert user_frame["author"] == {"user_id": "u-alice", "display_name": "Alice Liddell"}
        (confirmed,) = [m for m in ws.sent if m["type"] == "user_confirmed"]
        assert confirmed["author"] == {"user_id": "u-alice", "display_name": "Alice Liddell"}
        assert _frames(broker, "user_confirmed")[0]["author"]["user_id"] == "u-alice"

    @pytest.mark.asyncio
    async def test_the_trace_span_is_attributed_to_the_user(self, tmp_path):
        broker = _broker(tmp_path)
        broker._complete_trace_span = AsyncMock()
        ws = _connect(broker, BOB)

        await _say(broker, ws, "hello")

        span = broker._complete_trace_span.await_args.kwargs
        assert span["actor_id"] == "u-bob"
        assert span["actor_label"] == "Bob Stone"

    @pytest.mark.asyncio
    async def test_history_rows_carry_the_author_and_omit_it_when_there_is_none(self, tmp_path):
        broker = _broker(tmp_path)
        ws = _connect(broker, ALICE)
        await _say(broker, ws, "from alice")
        broker._conversation_turns.append(
            ConversationTurn(id="legacy", role="user", content="from before authorship")
        )

        rows = {row["id"]: row for row in conversation_rows(broker, omit_empty_participants=True)}

        (authored,) = [r for r in rows.values() if r["content"] == "from alice"]
        assert authored["author"] == {"user_id": "u-alice", "display_name": "Alice Liddell"}
        assert "author" not in rows["legacy"]

    @pytest.mark.asyncio
    async def test_a_connection_without_an_identity_records_no_author(self, tmp_path):
        broker = _broker(tmp_path)
        broker._complete_trace_span = AsyncMock()
        ws = _connect(broker, None)

        await _say(broker, ws, "who am i")

        (turn,) = [t for t in broker._conversation_turns if t.role == "user"]
        assert turn.author is None
        assert "author" not in _frames(broker, "user")[0]
        assert "author" not in [m for m in ws.sent if m["type"] == "user_confirmed"][0]
        assert broker._complete_trace_span.await_args.kwargs["actor_label"] == "user"

    @pytest.mark.asyncio
    async def test_an_internal_dispatch_has_no_author(self, tmp_path):
        broker = _broker(tmp_path)
        _connect(broker, ALICE)

        await broker._dispatch_browser_message({"content": "from telegram"}, sender_ws=None)
        await _settle_delivery()

        (turn,) = [t for t in broker._conversation_turns if t.role == "user"]
        assert turn.author is None


class TestReplayKeepsTheAuthor:
    @pytest.mark.asyncio
    async def test_a_log_rebuild_shows_who_said_each_message(self, tmp_path):
        broker = _broker(tmp_path)
        alice, bob = _connect(broker, ALICE), _connect(broker, BOB)
        await _say(broker, alice, "first")
        await _say(broker, bob, "second")

        frames = [
            SimpleNamespace(
                session_id="test-session",
                seq=entry["seq"],
                kind=str(entry["payload"].get("type")),
                payload=entry["payload"],
                request_id=entry.get("request_id"),
                ts=None,
            )
            for entry in broker._event_log_buffer
        ]
        turns = reduce_frames(frames).turns

        authors = [(t["content"], t.get("author")) for t in turns if t["role"] == "user"]
        assert authors == [
            ("first", {"user_id": "u-alice", "display_name": "Alice Liddell"}),
            ("second", {"user_id": "u-bob", "display_name": "Bob Stone"}),
        ]

    def test_old_frames_without_an_author_rebuild_with_none(self):
        frame = SimpleNamespace(
            session_id="s",
            seq=1,
            kind="user",
            payload={
                "type": "user",
                "role": "user",
                "uuid": "t-1",
                "message": {"role": "user", "content": "recorded long ago"},
            },
            request_id=None,
            ts=None,
        )

        (turn,) = reduce_frames([frame]).turns

        assert turn["content"] == "recorded long ago"
        assert "author" not in turn

    @pytest.mark.asyncio
    async def test_old_turns_on_disk_load_without_an_author(self, tmp_path):
        broker = _broker(tmp_path)
        path = broker._conversation_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "turns": [
                        {"id": "a", "role": "user", "content": "old"},
                        {
                            "id": "b",
                            "role": "user",
                            "content": "new",
                            "author": {"user_id": "u-bob", "display_name": "Bob Stone"},
                        },
                    ]
                }
            )
        )

        broker._load_conversation_history()

        assert [t.author for t in broker._conversation_turns] == [
            None,
            {"user_id": "u-bob", "display_name": "Bob Stone"},
        ]
        assert broker._human_author_ids == {"u-bob"}


class TestWhatTheModelReads:
    @pytest.mark.asyncio
    async def test_a_session_with_one_person_is_unchanged(self, tmp_path):
        broker = _broker(tmp_path)
        alice = _connect(broker, ALICE)

        await _say(broker, alice, "one")
        await _say(broker, alice, "two")

        sent = [c.args[0] for c in broker._transport.send_message.await_args_list]
        assert sent == ["one", "two"]

    @pytest.mark.asyncio
    async def test_every_message_is_named_once_a_second_person_speaks(self, tmp_path):
        broker = _broker(tmp_path)
        alice, bob = _connect(broker, ALICE), _connect(broker, BOB)

        await _say(broker, alice, "one")
        await _say(broker, bob, "two")
        await _say(broker, alice, "three")

        sent = [c.args[0] for c in broker._transport.send_message.await_args_list]
        assert sent == ["one", "[Bob Stone] two", "[Alice Liddell] three"]
        # What the people see is what they typed: the name rides in the author field.
        contents = [t.content for t in broker._conversation_turns if t.role == "user"]
        assert contents == ["one", "two", "three"]

    @pytest.mark.asyncio
    async def test_the_name_opens_the_message_ahead_of_retrieval_pointers(self, tmp_path):
        broker = _broker(tmp_path)
        broker._apply_retrieval_reflex = AsyncMock(side_effect=lambda m: f"[pointer]\n{m}")
        alice, bob = _connect(broker, ALICE), _connect(broker, BOB)
        await _say(broker, alice, "one")

        await _say(broker, bob, "two")

        assert broker._transport.send_message.await_args.args[0] == "[Bob Stone] [pointer]\ntwo"

    @pytest.mark.asyncio
    async def test_a_person_with_no_display_name_is_named_by_their_id(self, tmp_path):
        broker = _broker(tmp_path)
        alice = _connect(broker, ALICE)
        nameless = _connect(broker, MessageAuthor(user_id="u-carol"))

        await _say(broker, alice, "one")
        await _say(broker, nameless, "two")

        assert broker._transport.send_message.await_args.args[0] == "[u-carol] two"

    @pytest.mark.asyncio
    async def test_a_restarted_broker_remembers_that_two_people_have_spoken(self, tmp_path):
        broker = _broker(tmp_path)
        broker._restore_durable_authors(
            [
                SimpleNamespace(kind="user", payload={"author": ALICE.to_dict()}),
                SimpleNamespace(kind="user_confirmed", payload={"author": BOB.to_dict()}),
                SimpleNamespace(kind="user", payload={"message": {"content": "legacy"}}),
                SimpleNamespace(kind="assistant", payload={"author": {"user_id": "ignored"}}),
            ]
        )
        alice = _connect(broker, ALICE)

        await _say(broker, alice, "back again")

        assert broker._transport.send_message.await_args.args[0] == "[Alice Liddell] back again"

    @pytest.mark.asyncio
    async def test_a_steer_is_named_and_carries_the_author_for_merging(self, tmp_path):
        broker = _broker(tmp_path, steering_mode="live", steer=True)
        alice, bob = _connect(broker, ALICE), _connect(broker, BOB)
        await _say(broker, alice, "start")
        broker._transport.send_control.reset_mock()

        await _say(broker, bob, "change course")

        call = broker._transport.send_control.await_args
        assert call.args == ("steer",)
        assert call.kwargs["content"] == "[Bob Stone] change course"
        assert call.kwargs["author_name"] == "Bob Stone"

    @pytest.mark.asyncio
    async def test_a_single_person_steer_is_unchanged(self, tmp_path):
        broker = _broker(tmp_path, steering_mode="live", steer=True)
        alice = _connect(broker, ALICE)

        await _say(broker, alice, "change course")

        call = broker._transport.send_control.await_args
        assert call.kwargs["content"] == "change course"
        assert "author_name" not in call.kwargs


class TestRoomMessages:
    def _room_broker(self, tmp_path) -> Broker:
        broker = _broker(tmp_path)
        broker._room_bridge = MagicMock()
        broker._room_bridge.participants = {}
        return broker

    def test_the_room_wrapper_names_the_sender(self, tmp_path):
        broker = self._room_broker(tmp_path)

        text = broker._format_room_message_for_skuld("deploy it", ALICE)

        assert "Human message from Alice Liddell via an external communication interface:" in text
        assert text.endswith("deploy it")

    def test_the_room_wrapper_is_unchanged_without_an_author(self, tmp_path):
        broker = self._room_broker(tmp_path)

        text = broker._format_room_message_for_skuld("deploy it")

        assert "Human message from an external communication interface:" in text

    @pytest.mark.asyncio
    async def test_a_human_room_message_records_its_author(self, tmp_path):
        broker = self._room_broker(tmp_path)
        broker._complete_trace_span = AsyncMock()
        broker._send_explicit_human_message_to_transport = AsyncMock()

        await broker.handle_human_room_message("hi room", source="room_viewer", author=BOB)

        (turn,) = broker._conversation_turns
        assert turn.author == BOB.to_dict()
        assert broker._complete_trace_span.await_args.kwargs["actor_id"] == "u-bob"
        broker._send_explicit_human_message_to_transport.assert_awaited_once_with("hi room", BOB)
        (confirmed,) = _frames(broker, "user_confirmed")
        assert confirmed["author"] == BOB.to_dict()

    @pytest.mark.asyncio
    async def test_a_relayed_room_message_has_no_author(self, tmp_path):
        broker = self._room_broker(tmp_path)
        broker._send_explicit_human_message_to_transport = AsyncMock()

        await broker.handle_human_room_message("relayed", source="telegram")

        assert broker._conversation_turns[0].author is None
        assert "author" not in _frames(broker, "user_confirmed")[0]


class TestAuthorFromHeaders:
    def test_reads_the_stamped_identity_and_decodes_the_name(self):
        author = author_from_headers(
            {"X-Auth-User-Id": " u-jose ", "x-auth-display-name": "Jos%C3%A9%20%C3%91and%C3%BA"}
        )

        assert author == MessageAuthor(user_id="u-jose", display_name="José Ñandú")

    def test_no_user_id_means_no_author(self):
        assert author_from_headers({"x-auth-display-name": "Mallory"}) is None

    def test_a_missing_name_stays_empty(self):
        assert author_from_headers({"x-auth-user-id": "u-1"}) == MessageAuthor(user_id="u-1")

    def test_the_header_names_come_from_configuration(self):
        author = author_from_headers(
            {"x-id": "u-9", "x-name": "Nine"}, user_id_header="X-Id", display_name_header="X-Name"
        )

        assert author == MessageAuthor(user_id="u-9", display_name="Nine")


class TestMessageAuthor:
    @pytest.mark.parametrize(
        "raw",
        [None, "alice", [], {}, {"user_id": ""}, {"user_id": "  "}, {"user_id": 7}],
    )
    def test_anything_that_is_not_an_author_is_no_author(self, raw):
        assert MessageAuthor.from_wire(raw) is None

    def test_roundtrips_through_the_wire_shape(self):
        assert MessageAuthor.from_wire(ALICE.to_dict()) == ALICE

    def test_a_non_string_display_name_is_empty(self):
        assert MessageAuthor.from_wire({"user_id": "u", "display_name": 5}) == MessageAuthor("u")
