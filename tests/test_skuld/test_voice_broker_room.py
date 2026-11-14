"""The broker's hosted room as a voice call sees it (``skuld.voice.broker_room``)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from niuu.domain.message_author import MessageAuthor
from skuld.config import VoiceMemberConfig
from skuld.conversation_models import ConversationTurn
from skuld.voice.broker_room import BrokerVoiceRoom
from skuld.voice.broker_session import VOICE_CALL_REQUEST_PREFIX, VoiceRecordError
from skuld.voice.room_port import PostKind, RoomKind, RoomPost
from skuld.voice.session_port import SendStatus
from tests.test_skuld.voice_support import OWNER

ADA = MessageAuthor(user_id=OWNER, display_name="Ada")


def participant(peer_id: str, *, display_name: str = "", persona: str | None = "") -> Any:
    return SimpleNamespace(peer_id=peer_id, display_name=display_name, persona=persona)


class FakeRoomBroker:
    """The attributes of the real broker that ``BrokerVoiceRoom`` reads."""

    def __init__(self, *, untargeted_delivery: str = "everyone") -> None:
        self._settings = SimpleNamespace(
            session=SimpleNamespace(name="Release crew"),
            room=SimpleNamespace(environment_id="env-7", untargeted_delivery=untargeted_delivery),
            voice=SimpleNamespace(members={}),
        )
        self.participants: dict[str, Any] = {}
        self._room_bridge: Any = SimpleNamespace(participants=self.participants)
        self.agent_peer_ids: list[str] = []
        self.untargeted_peer_ids: list[str] = []
        self.people: list[dict[str, str]] = []
        self._conversation_turns: list[ConversationTurn] = []
        self.directed: list[dict[str, Any]] = []
        #: peer id -> the exception its delivery raises.
        self.delivery_errors: dict[str, Exception] = {}
        self.enqueued: list[dict[str, Any]] = []
        self.enqueue_result: dict[str, Any] | None = {"seq": 1}

    def seat(self, peer_id: str, *, agent: bool = True, **fields: Any) -> Any:
        seated = participant(peer_id, **fields)
        self.participants[peer_id] = seated
        if agent:
            self.agent_peer_ids.append(peer_id)
            self.untargeted_peer_ids.append(peer_id)
        return seated

    def _room_agent_peer_ids(self) -> list[str]:
        return list(self.agent_peer_ids)

    def _untargeted_room_peer_ids(self) -> list[str]:
        return list(self.untargeted_peer_ids)

    def connected_people(self) -> list[dict[str, str]]:
        return list(self.people)

    async def handle_directed_room_message(self, peer_id: str, text: str, **kwargs: Any) -> str:
        if peer_id in self.delivery_errors:
            raise self.delivery_errors[peer_id]
        self.directed.append({"peer_id": peer_id, "text": text, **kwargs})
        return "msg-1"

    def _enqueue_event_log(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        self.enqueued.append(frame)
        return self.enqueue_result


@pytest.fixture
def broker() -> FakeRoomBroker:
    return FakeRoomBroker()


def _room(broker: FakeRoomBroker) -> BrokerVoiceRoom:
    return BrokerVoiceRoom(broker)


def _turn(turn_id: str = "t1", **fields: Any) -> ConversationTurn:
    values: dict[str, Any] = {"id": turn_id, "role": "assistant", "content": "Done."}
    values.update(fields)
    return ConversationTurn(**values)


def _agent_turn(**fields: Any) -> ConversationTurn:
    return _turn(
        participant_id="peer-r",
        participant_meta={"display_name": "Reviewer", "persona": "code-reviewer"},
        **fields,
    )


class TestStatus:
    def test_a_configured_member_not_seated_is_not_here(self, broker):
        broker._settings.voice.members = {
            "Reviewer": VoiceMemberConfig(
                persona="code-reviewer", lead=True, voices={"grok-voice": "rex"}
            )
        }

        status = _room(broker).status()

        [member] = status.members
        assert (member.name, member.persona, member.lead) == ("Reviewer", "code-reviewer", True)
        assert member.present is False
        assert member.voices == {"grok-voice": "rex"}

    def test_a_configured_member_that_is_seated_is_here(self, broker):
        broker._settings.voice.members = {"Reviewer": VoiceMemberConfig(voices={})}
        broker.seat("peer-r", display_name="Reviewer", persona="code-reviewer")

        status = _room(broker).status()

        [member] = status.members
        assert member.present is True
        # The configured persona (empty here) is Forge's word, not the mesh's.
        assert member.persona == ""

    def test_a_seated_agent_nobody_configured_is_added_as_present(self, broker):
        broker._settings.voice.members = {"Reviewer": VoiceMemberConfig(voices={})}
        broker.seat("peer-r", display_name="Reviewer")
        broker.seat("peer-x", display_name="Stranger", persona="scout")

        status = _room(broker).status()

        assert [(m.name, m.persona, m.present, m.lead, m.voices) for m in status.members] == [
            ("Reviewer", "", True, False, {}),
            ("Stranger", "scout", True, False, {}),
        ]

    def test_a_seated_agent_with_no_name_is_known_by_persona_then_peer_id(self, broker):
        broker.seat("peer-1", persona="builder")
        broker.seat("peer-2", persona=None)

        status = _room(broker).status()

        assert [(m.name, m.persona) for m in status.members] == [
            ("builder", "builder"),
            ("peer-2", ""),
        ]

    def test_participants_that_are_not_agents_are_not_members(self, broker):
        broker.seat("peer-ada", agent=False, display_name="Ada")
        broker.seat("peer-r", display_name="Reviewer")

        status = _room(broker).status()

        assert [m.name for m in status.members] == ["Reviewer"]

    def test_a_broker_without_a_room_bridge_seats_nobody(self, broker):
        broker._room_bridge = None
        broker._settings.voice.members = {"Reviewer": VoiceMemberConfig(voices={})}

        status = _room(broker).status()

        assert [(m.name, m.present) for m in status.members] == [("Reviewer", False)]

    def test_the_room_is_named_and_identified_from_the_settings(self, broker):
        status = _room(broker).status()

        assert (status.room_id, status.name) == ("env-7", "Release crew")

    def test_everyone_delivery_is_a_team_room(self, broker):
        assert _room(broker).status().kind is RoomKind.TEAM

    def test_refuse_delivery_is_a_run_room(self):
        assert _room(FakeRoomBroker(untargeted_delivery="refuse")).status().kind is RoomKind.RUN

    def test_people_online_are_named_by_display_name_then_user_id_and_blanks_dropped(self, broker):
        broker.people = [
            {"user_id": "u1", "display_name": "Ada"},
            {"user_id": "u2", "display_name": ""},
            {"user_id": "", "display_name": ""},
        ]

        assert _room(broker).status().people_online == ("Ada", "u2")


class TestPosts:
    def _posts(self, broker, *turns) -> list[RoomPost]:
        broker._conversation_turns = list(turns)
        return _room(broker).posts()

    def test_an_agents_reply_is_an_answer_by_its_display_name(self, broker):
        [post] = self._posts(broker, _agent_turn(created_at="2026-11-14T10:00:00+00:00"))

        assert post == RoomPost(
            post_id="t1",
            speaker="Reviewer",
            kind=PostKind.ANSWER,
            text="Done.",
            persona="code-reviewer",
            created_at="2026-11-14T10:00:00+00:00",
        )

    def test_the_speaker_falls_back_to_persona_then_participant_id(self, broker):
        posts = self._posts(
            broker,
            _turn("1", participant_id="p1", participant_meta={"persona": "builder"}),
            _turn("2", participant_id="p2", participant_meta=None),
        )

        assert [(p.speaker, p.persona) for p in posts] == [("builder", "builder"), ("p2", "")]

    @pytest.mark.parametrize(
        ("metadata", "kind"),
        [
            ({}, PostKind.ANSWER),
            ({"error": "boom"}, PostKind.ERROR),
            ({"messageType": "error"}, PostKind.ERROR),
            ({"message_type": "error"}, PostKind.ERROR),
            ({"messageType": "question"}, PostKind.QUESTION),
            ({"message_type": "help_needed"}, PostKind.QUESTION),
            ({"messageType": "notice"}, PostKind.NOTICE),
            ({"messageType": "room_notice"}, PostKind.NOTICE),
            ({"messageType": "mesh_message"}, PostKind.NOTICE),
            ({"messageType": "delegation"}, PostKind.NOTICE),
            ({"messageType": "outcome"}, PostKind.NOTICE),
            ({"messageType": "chat"}, PostKind.ANSWER),
        ],
    )
    def test_an_agents_post_is_classified_from_its_metadata(self, broker, metadata, kind):
        [post] = self._posts(broker, _agent_turn(metadata=metadata))

        assert post.kind is kind

    def test_a_persons_message_is_a_person_post_by_their_label(self, broker):
        [post] = self._posts(
            broker,
            _turn(role="user", content="Status?", author=ADA.to_dict()),
        )

        assert (post.speaker, post.kind, post.text, post.persona) == (
            "Ada",
            PostKind.PERSON,
            "Status?",
            "",
        )

    def test_a_user_turn_with_an_unreadable_author_is_still_a_person(self, broker):
        [post] = self._posts(broker, _turn(role="user", author={"display_name": "no id"}))

        assert (post.speaker, post.kind) == ("a person", PostKind.PERSON)

    def test_a_turn_from_no_participant_and_no_author_is_a_room_notice(self, broker):
        posts = self._posts(
            broker,
            _turn("1", content="Review step started"),
            _turn("2", role="user", content="Hello"),
        )

        assert [(p.speaker, p.kind, p.text) for p in posts] == [
            ("room", PostKind.NOTICE, "Review step started"),
            ("room", PostKind.NOTICE, "Hello"),
        ]

    def test_what_the_call_itself_sent_is_skipped(self, broker):
        posts = self._posts(
            broker,
            _turn(
                "1",
                role="user",
                author=ADA.to_dict(),
                metadata={"request_id": f"{VOICE_CALL_REQUEST_PREFIX}abc"},
            ),
            _turn("2", role="user", author=ADA.to_dict(), metadata={"request_id": "web-1"}),
        )

        assert [p.post_id for p in posts] == ["2"]

    @pytest.mark.parametrize(
        "fields",
        [
            {"visibility": "internal"},
            {"visibility": "private"},
            {"content": ""},
            {"content": "  \n "},
        ],
    )
    def test_non_public_and_empty_turns_are_not_posts(self, broker, fields):
        assert self._posts(broker, _agent_turn(**fields)) == []

    def test_posts_keep_the_rooms_order(self, broker):
        posts = self._posts(broker, _turn("1"), _turn("2"), _turn("3"))

        assert [p.post_id for p in posts] == ["1", "2", "3"]


class TestSayTo:
    async def test_an_agent_not_seated_is_a_failure_and_nothing_is_sent(self, broker):
        outcome = await _room(broker).say_to("Ghost", "Hi", author=ADA, model_note="n")

        assert outcome.status is SendStatus.FAILED
        assert outcome.reason == "Ghost is not in the room right now"
        assert broker.directed == []

    async def test_delivered_as_the_person_through_the_rooms_directed_delivery(self, broker):
        broker.seat("peer-r", display_name="Reviewer")

        outcome = await _room(broker).say_to(
            "Reviewer", "Please re-check", author=ADA, model_note="[note]"
        )

        assert outcome.status is SendStatus.DELIVERED
        [call] = broker.directed
        request_id = call.pop("request_id")
        assert request_id.startswith(VOICE_CALL_REQUEST_PREFIX)
        assert len(request_id) > len(VOICE_CALL_REQUEST_PREFIX)
        assert call == {
            "peer_id": "peer-r",
            "text": "Please re-check",
            "source": "voice",
            "author": ADA,
            "model_note": "[note]",
        }

    async def test_each_delivery_has_its_own_request_id(self, broker):
        broker.seat("peer-r", display_name="Reviewer")
        room = _room(broker)

        await room.say_to("Reviewer", "one", author=ADA, model_note="")
        await room.say_to("Reviewer", "two", author=ADA, model_note="")

        assert broker.directed[0]["request_id"] != broker.directed[1]["request_id"]

    @pytest.mark.parametrize(
        "error",
        [
            LookupError("no such participant"),
            RuntimeError("Room mode is not enabled"),
            ValueError("blank"),
        ],
    )
    async def test_a_refused_delivery_is_a_failure_with_the_reason_and_logged(
        self, broker, error, caplog
    ):
        broker.seat("peer-r", display_name="Reviewer")
        broker.delivery_errors["peer-r"] = error

        with caplog.at_level(logging.ERROR, logger="skuld.voice"):
            outcome = await _room(broker).say_to("Reviewer", "Hi", author=ADA, model_note="")

        assert outcome.status is SendStatus.FAILED
        assert outcome.reason == str(error)
        assert "could not reach room participant peer-r" in caplog.text

    async def test_an_unexpected_error_is_not_swallowed(self, broker):
        broker.seat("peer-r", display_name="Reviewer")
        broker.delivery_errors["peer-r"] = TypeError("bug")

        with pytest.raises(TypeError, match="bug"):
            await _room(broker).say_to("Reviewer", "Hi", author=ADA, model_note="")


class TestSayToEveryone:
    async def test_a_run_room_refuses_and_sends_nothing(self):
        broker = FakeRoomBroker(untargeted_delivery="refuse")
        broker.seat("peer-r", display_name="Reviewer")

        outcome = await _room(broker).say_to_everyone("Hi", author=ADA, model_note="")

        assert outcome.status is SendStatus.FAILED
        assert outcome.reason == "this room takes messages for one agent at a time"
        assert broker.directed == []

    async def test_a_room_with_no_agents_has_no_one_to_tell(self, broker):
        outcome = await _room(broker).say_to_everyone("Hi", author=ADA, model_note="")

        assert outcome.status is SendStatus.FAILED
        assert outcome.reason == "no agent is in the room right now"

    async def test_one_directed_message_per_agent_all_from_the_person(self, broker):
        broker.seat("peer-r", display_name="Reviewer")
        broker.seat("peer-b", display_name="Builder")

        outcome = await _room(broker).say_to_everyone("Ship it", author=ADA, model_note="[n]")

        assert outcome.status is SendStatus.DELIVERED
        assert [
            (c["peer_id"], c["text"], c["author"], c["model_note"]) for c in broker.directed
        ] == [
            ("peer-r", "Ship it", ADA, "[n]"),
            ("peer-b", "Ship it", ADA, "[n]"),
        ]
        assert all(c["request_id"].startswith(VOICE_CALL_REQUEST_PREFIX) for c in broker.directed)
        assert len({c["request_id"] for c in broker.directed}) == 2

    async def test_a_partial_failure_is_a_failure_naming_who_missed_it_and_why(self, broker):
        broker.seat("peer-r", display_name="Reviewer")
        broker.seat("peer-b", display_name="Builder")
        broker.seat("peer-s", display_name="Scout")
        broker.delivery_errors["peer-b"] = RuntimeError("busy")
        broker.delivery_errors["peer-s"] = LookupError("gone")

        outcome = await _room(broker).say_to_everyone("Ship it", author=ADA, model_note="")

        assert outcome.status is SendStatus.FAILED
        assert outcome.reason == ("delivered to 1 of 3 agents; not to Builder (busy), Scout (gone)")
        # Every agent was tried: one failing does not stop the rest.
        assert [c["peer_id"] for c in broker.directed] == ["peer-r"]

    async def test_every_agent_is_tried_even_after_a_failure(self, broker):
        broker.seat("peer-b", display_name="Builder")
        broker.seat("peer-r", display_name="Reviewer")
        broker.delivery_errors["peer-b"] = RuntimeError("busy")

        outcome = await _room(broker).say_to_everyone("Go", author=ADA, model_note="")

        assert outcome.reason == "delivered to 1 of 2 agents; not to Builder (busy)"
        assert [c["peer_id"] for c in broker.directed] == ["peer-r"]

    async def test_a_target_that_is_not_seated_is_named_by_its_peer_id(self, broker):
        broker.untargeted_peer_ids = ["ghost-peer"]
        broker.delivery_errors["ghost-peer"] = LookupError("unknown")

        outcome = await _room(broker).say_to_everyone("Go", author=None, model_note="")

        assert outcome.reason == "delivered to 0 of 1 agents; not to ghost-peer (unknown)"


class TestHearing:
    def test_a_new_public_turn_reaches_the_listener_as_a_post(self, broker):
        heard: list[RoomPost] = []
        room = _room(broker)
        room.listen(heard.append)

        room.observe_turn(_agent_turn())

        assert [(p.post_id, p.speaker, p.kind) for p in heard] == [
            ("t1", "Reviewer", PostKind.ANSWER)
        ]

    def test_a_turn_that_is_not_a_post_is_not_passed_on(self, broker):
        heard: list[RoomPost] = []
        room = _room(broker)
        room.listen(heard.append)

        room.observe_turn(_agent_turn(visibility="internal"))
        room.observe_turn(_agent_turn(content=" "))
        room.observe_turn(
            _turn(role="user", author=ADA.to_dict(), metadata={"request_id": "voice-call-1"})
        )

        assert heard == []

    def test_without_a_listener_a_turn_is_ignored(self, broker):
        _room(broker).observe_turn(_agent_turn())

    def test_a_stopped_listener_hears_nothing(self, broker):
        heard: list[RoomPost] = []
        room = _room(broker)
        room.listen(heard.append)
        room.listen(None)

        room.observe_turn(_agent_turn())
        room.observe_question("peer-r", {"data": "Which db?"})

        assert heard == []

    def test_a_question_from_a_seated_agent_is_a_question_post_in_its_name(self, broker):
        broker.seat("peer-r", display_name="Reviewer", persona="code-reviewer")
        heard: list[RoomPost] = []
        room = _room(broker)
        room.listen(heard.append)

        room.observe_question("peer-r", {"id": "help-9", "data": "  Which database?  "})

        assert heard == [
            RoomPost(
                post_id="help-9",
                speaker="Reviewer",
                kind=PostKind.QUESTION,
                text="Which database?",
                persona="code-reviewer",
            )
        ]

    def test_a_question_may_carry_its_text_as_content(self, broker):
        heard: list[RoomPost] = []
        room = _room(broker)
        room.listen(heard.append)

        room.observe_question("peer-x", {"content": "Which region?"})

        [post] = heard
        assert (post.text, post.speaker, post.persona) == ("Which region?", "peer-x", "")
        assert len(post.post_id) == 32

    def test_a_question_without_a_room_bridge_is_named_by_its_peer_id(self, broker):
        broker._room_bridge = None
        heard: list[RoomPost] = []
        room = _room(broker)
        room.listen(heard.append)

        room.observe_question("peer-x", {"data": "Which region?"})

        assert heard[0].speaker == "peer-x"

    def test_a_question_with_no_text_is_not_a_post(self, broker):
        heard: list[RoomPost] = []
        room = _room(broker)
        room.listen(heard.append)

        room.observe_question("peer-r", {"data": "  "})
        room.observe_question("peer-r", {})

        assert heard == []

    def test_without_a_listener_a_question_is_ignored(self, broker):
        _room(broker).observe_question("peer-r", {"data": "Which db?"})


class TestRecord:
    def test_a_frame_goes_to_the_hosts_durable_event_log(self, broker):
        _room(broker).record({"type": "voice_call", "event": "started"})

        assert broker.enqueued == [{"type": "voice_call", "event": "started"}]

    def test_a_log_that_is_off_is_an_error_naming_the_remedy(self, broker):
        broker.enqueue_result = None

        with pytest.raises(VoiceRecordError, match="event_log_enabled and volundr_api_url"):
            _room(broker).record({"type": "voice_call"})
