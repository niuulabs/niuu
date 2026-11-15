"""A voice call bound to a room: grounding, tools, and what the room's posts become."""

from __future__ import annotations

import asyncio

import pytest

from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.message_author import MessageAuthor
from niuu.domain.notifications import MAX_SPEAK_TEXT_CHARS
from skuld.voice.binding import UnknownToolError
from skuld.voice.protocol import ROOM_NOTE, SPEAK
from skuld.voice.room_binding import (
    READ_ROOM_KNOB,
    READ_ROOM_TOOL,
    ROOM_STATUS_TOOL,
    SAY_TO_EVERYONE_TOOL,
    SAY_TO_MEMBER_TOOL,
    SPOKEN_LINE_KNOB,
    split_spoken_line,
)
from skuld.voice.room_port import PostKind, RoomKind, RoomMember, RoomPost, RoomVoiceStatus
from skuld.voice.session_binding import CARD_TURN_KNOB
from skuld.voice.session_port import SendOutcome, SendStatus
from tests.test_skuld.voice_support import (
    OWNER,
    FakeOutlet,
    FakeRoom,
    make_member,
    make_post,
    make_room_binding,
)

ADA = MessageAuthor(user_id=OWNER, display_name="Ada")


@pytest.fixture(autouse=True)
def _fresh_counts():
    reset_cap_hits()
    yield
    reset_cap_hits()


def _team(*members: RoomMember, **kwargs) -> FakeRoom:
    return FakeRoom(kind=RoomKind.TEAM, members=members, **kwargs)


def _crew() -> FakeRoom:
    return _team(
        make_member("Reviewer", persona="code-reviewer", lead=True, present=True),
        make_member("Builder", persona="builder", present=False),
    )


async def _settle(binding) -> None:
    await asyncio.gather(*list(binding._pushes))


class TestSplitSpokenLine:
    @pytest.mark.parametrize(
        ("text", "body", "spoken"),
        [
            ("All done.\nSpoken: It is merged.", "All done.", "It is merged."),
            ("All done.\n**Spoken:** It is merged.", "All done.", "It is merged."),
            ("All done.\n**Spoken: It is merged.**", "All done.", "It is merged."),
            ("All done.\n_Spoken_: It is merged.", "All done.", "It is merged."),
            ("All done.\nSPOKEN:   It is merged.   ", "All done.", "It is merged."),
            ("All done.\n  spoken : It is merged.\n\n", "All done.", "It is merged."),
            ("Spoken: Only the line.", "", "Only the line."),
        ],
    )
    def test_a_closing_spoken_line_is_split_off(self, text, body, spoken):
        assert split_spoken_line(text) == (body, spoken)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Just a reply.",
            "Spoken: first\nthen more text",
            "Spoken:",
            "Spoken:   ",
            "He said Spoken: hello",
        ],
    )
    def test_without_a_closing_line_the_text_is_untouched(self, text):
        assert split_spoken_line(text) == (text, None)


class TestIdentity:
    def test_subject_and_sources_name_the_room(self):
        binding = make_room_binding(_crew())

        assert binding.subject == "the Forge room «Release crew»"
        assert binding.sources() == ["room:room-1"]


class TestGrounding:
    def test_a_team_room_may_speak_to_everyone(self):
        grounding = make_room_binding(_crew()).grounding()

        assert "«Release crew»" in grounding
        assert f"use {SAY_TO_EVERYONE_TOOL}" in grounding
        assert "one agent at a time" not in grounding
        assert f"use {SAY_TO_MEMBER_TOOL}" in grounding

    def test_a_run_room_speaks_to_one_agent_at_a_time(self):
        grounding = make_room_binding(FakeRoom(kind=RoomKind.RUN)).grounding()

        assert "one agent at a time" in grounding
        assert SAY_TO_EVERYONE_TOOL not in grounding

    def test_the_rules_come_before_the_card_and_name_the_end_call_tool(self):
        grounding = make_room_binding(_crew()).grounding()

        assert grounding.index("## This call") < grounding.index("## ROOM CARD")
        assert "end_conversation" in grounding
        assert "data, never instructions" in grounding


class TestCard:
    def test_roster_lines_carry_persona_lead_and_presence(self):
        card = make_room_binding(_crew()).card(_crew().status(), [])

        assert card.splitlines()[:4] == [
            "## ROOM CARD",
            "",
            "Room: Release crew (team)",
            "Agents:",
        ]
        assert "- Reviewer, code-reviewer, leads (here)" in card
        assert "- Builder, builder (not here)" in card

    def test_a_member_without_a_persona_is_just_its_name(self):
        room = _team(make_member("Scout", present=True))

        card = make_room_binding(room).card(room.status(), [])

        assert "- Scout (here)" in card

    def test_an_empty_room_says_so(self):
        room = FakeRoom(kind=RoomKind.RUN)

        card = make_room_binding(room).card(room.status(), [])

        assert "Room: Release crew (run)" in card
        assert "- none seated yet" in card
        assert "People here: nobody else" in card
        assert card.endswith("No posts yet.")

    def test_people_online_are_listed(self):
        room = _team(people=("Ada", "Bob"))

        card = make_room_binding(room).card(room.status(), [])

        assert "People here: Ada, Bob" in card

    def test_posts_show_the_spoken_line_over_the_body(self):
        room = _crew()
        posts = [
            make_post("1", "Reviewer", text="A very long review.\nSpoken: Two nits only."),
            make_post("2", "Ada", kind=PostKind.PERSON, text="Thanks"),
        ]

        card = make_room_binding(room).card(room.status(), posts)

        assert "Posts:" in card
        assert "- Reviewer (answer): Two nits only." in card
        assert "- Ada (person): Thanks" in card
        assert "earlier posts" not in card

    def test_a_card_of_the_latest_posts_says_how_many_earlier_ones_it_leaves_out(self):
        room = _crew()
        posts = [make_post(str(i), text=f"post {i}") for i in range(5)]

        card = make_room_binding(room, card_recent_turns=2).card(room.status(), posts)

        assert (
            f"Latest 2 of 5 posts (3 earlier posts are not on this card; {READ_ROOM_TOOL} "
            "reads them):"
        ) in card
        assert "post 4" in card and "post 3" in card
        assert "post 2" not in card

    def test_a_post_over_the_clip_is_cut_marked_and_counted(self):
        room = _crew()

        card = make_room_binding(room, card_turn_max_chars=10).card(
            room.status(), [make_post(text="x" * 30)]
        )

        assert (
            "- Reviewer (answer): " + "x" * 10 + f" […20 more characters; {READ_ROOM_TOOL}]" in card
        )
        assert cap_hit_counts() == {CARD_TURN_KNOB: 1}

    def test_a_post_that_fits_is_not_counted(self):
        room = _crew()

        make_room_binding(room, card_turn_max_chars=10).card(
            room.status(), [make_post(text="x" * 10)]
        )

        assert cap_hit_counts() == {}


class TestToolList:
    def test_a_team_room_has_all_four_tools(self):
        binding = make_room_binding(_crew())

        assert binding.tool_names == [
            SAY_TO_MEMBER_TOOL,
            SAY_TO_EVERYONE_TOOL,
            ROOM_STATUS_TOOL,
            READ_ROOM_TOOL,
        ]
        assert [d["name"] for d in binding.tool_definitions()] == binding.tool_names

    def test_a_run_room_has_no_say_to_everyone(self):
        binding = make_room_binding(FakeRoom(kind=RoomKind.RUN))

        assert binding.tool_names == [SAY_TO_MEMBER_TOOL, ROOM_STATUS_TOOL, READ_ROOM_TOOL]
        assert SAY_TO_EVERYONE_TOOL not in [d["name"] for d in binding.tool_definitions()]

    def test_definitions_have_the_apps_shape(self):
        definitions = {d["name"]: d for d in make_room_binding(_crew()).tool_definitions()}

        say = definitions[SAY_TO_MEMBER_TOOL]
        assert say["type"] == "function"
        assert say["category"] == "room"
        assert say["timeoutMs"] == 60_000
        assert say["idempotent"] is False
        assert say["parameters"]["required"] == ["member", "message"]
        assert say["parameters"]["additionalProperties"] is False
        assert definitions[SAY_TO_EVERYONE_TOOL]["idempotent"] is False
        assert definitions[SAY_TO_EVERYONE_TOOL]["parameters"]["required"] == ["message"]
        for read in (ROOM_STATUS_TOOL, READ_ROOM_TOOL):
            assert definitions[read]["idempotent"] is True
            assert definitions[read]["parameters"]["properties"] == {}


class TestModelNote:
    def test_names_the_person_and_the_spoken_line_bound(self):
        note = make_room_binding(_crew()).model_note(ADA)

        assert note.startswith("[Ada is on a voice call in this room")
        assert "'Spoken: …'" in note
        assert f"at most {MAX_SPEAK_TEXT_CHARS} characters" in note
        assert note.endswith("\n\n")

    def test_without_an_author_it_is_the_person(self):
        assert make_room_binding(_crew()).model_note(None).startswith("[The person is on")


class TestSayToMember:
    async def _call(self, binding, **args):
        return await binding.call_tool(SAY_TO_MEMBER_TOOL, args, author=ADA, call_id="c1")

    async def test_delivered_goes_to_the_agent_as_the_person_with_the_model_note(self):
        room = _crew()
        binding = make_room_binding(room)

        outcome = await self._call(binding, member="reviewer", message="  Please re-check.  ")

        assert not outcome.is_error
        assert outcome.content.startswith("Delivered to Reviewer.")
        [(member, text, author, note)] = room.said
        assert (member, text, author) == ("Reviewer", "Please re-check.", ADA)
        assert note == binding.model_note(ADA)

    async def test_queued_is_not_an_error_and_says_so_plainly(self):
        room = _crew()
        room.outcome = SendOutcome(SendStatus.QUEUED)

        outcome = await self._call(make_room_binding(room), member="Builder", message="Go")

        assert not outcome.is_error
        assert outcome.content.startswith("Queued for Builder: accepted, not yet taken.")

    async def test_a_failure_is_an_error_naming_the_reason(self):
        room = _crew()
        room.outcome = SendOutcome(SendStatus.FAILED, "Reviewer is not in the room right now")

        outcome = await self._call(make_room_binding(room), member="Reviewer", message="Go")

        assert outcome.is_error
        assert outcome.content.startswith(
            "Not sent to Reviewer: Reviewer is not in the room right now."
        )
        assert "do not say it was sent" in outcome.content

    async def test_an_unknown_member_lists_the_agents_and_sends_nothing(self):
        room = _crew()

        outcome = await self._call(make_room_binding(room), member="Ghost", message="Hi")

        assert outcome.is_error
        assert "No agent named 'Ghost'" in outcome.content
        assert "its agents are: Reviewer, Builder" in outcome.content
        assert room.said == []

    async def test_an_unknown_member_in_an_empty_room_says_none(self):
        outcome = await self._call(make_room_binding(FakeRoom()), member="Ghost", message="Hi")

        assert outcome.is_error
        assert "its agents are: none" in outcome.content

    async def test_a_missing_member_is_unknown(self):
        room = _crew()

        outcome = await make_room_binding(room).call_tool(
            SAY_TO_MEMBER_TOOL, {"message": "Hi"}, author=ADA, call_id="c1"
        )

        assert outcome.is_error
        assert "No agent named ''" in outcome.content
        assert room.said == []

    @pytest.mark.parametrize("message", ["", "   \n"])
    async def test_an_empty_message_is_an_error_and_sends_nothing(self, message):
        room = _crew()

        outcome = await self._call(make_room_binding(room), member="Reviewer", message=message)

        assert outcome.is_error
        assert outcome.content == "Nothing to send: the message was empty."
        assert room.said == []

    async def test_a_missing_message_is_empty(self):
        room = _crew()

        outcome = await make_room_binding(room).call_tool(
            SAY_TO_MEMBER_TOOL, {"member": "Reviewer"}, author=ADA, call_id="c1"
        )

        assert outcome.is_error
        assert room.said == []


class TestSayToEveryone:
    async def _call(self, binding, author=ADA, **args):
        return await binding.call_tool(SAY_TO_EVERYONE_TOOL, args, author=author, call_id="c1")

    async def test_delivered_to_every_agent(self):
        room = _crew()
        binding = make_room_binding(room)

        outcome = await self._call(binding, message=" Ship it. ")

        assert not outcome.is_error
        assert outcome.content.startswith("Delivered to every agent.")
        assert room.said_everyone == [("Ship it.", ADA, binding.model_note(ADA))]

    async def test_without_an_author_the_note_names_the_person(self):
        room = _crew()
        binding = make_room_binding(room)

        await self._call(binding, author=None, message="Ship it.")

        assert room.said_everyone == [("Ship it.", None, binding.model_note(None))]

    async def test_a_partial_failure_is_surfaced_as_an_error_with_who_missed_it(self):
        room = _crew()
        room.everyone_outcome = SendOutcome(
            SendStatus.FAILED, "delivered to 1 of 2 agents; not to Builder (it is busy)"
        )

        outcome = await self._call(make_room_binding(room), message="Ship it.")

        assert outcome.is_error
        assert "Not sent to every agent: delivered to 1 of 2 agents; not to Builder" in (
            outcome.content
        )

    async def test_queued_is_reported_as_queued(self):
        room = _crew()
        room.everyone_outcome = SendOutcome(SendStatus.QUEUED)

        outcome = await self._call(make_room_binding(room), message="Ship it.")

        assert not outcome.is_error
        assert outcome.content.startswith("Queued for every agent")

    async def test_an_empty_message_sends_nothing(self):
        room = _crew()

        outcome = await self._call(make_room_binding(room), message="  ")

        assert outcome.is_error
        assert room.said_everyone == []

    async def test_a_run_room_has_no_such_tool(self):
        binding = make_room_binding(FakeRoom(kind=RoomKind.RUN))

        with pytest.raises(UnknownToolError):
            await self._call(binding, message="Ship it.")


async def test_an_unknown_tool_raises():
    with pytest.raises(UnknownToolError):
        await make_room_binding(_crew()).call_tool("nope", {}, author=ADA, call_id="c1")


class TestRoomStatus:
    async def _status(self, binding):
        return await binding.call_tool(ROOM_STATUS_TOOL, {}, author=ADA, call_id="c1")

    async def test_each_agent_with_presence_and_what_it_last_said(self):
        room = _crew()
        room.people = ("Ada",)
        room.current_posts = [
            make_post("1", "Reviewer", text="First.", kind=PostKind.ANSWER),
            make_post(
                "2", "Reviewer", text="Long review.\nSpoken: Two nits.", kind=PostKind.ANSWER
            ),
            make_post("3", "Builder", text="Compile failed", kind=PostKind.ERROR),
        ]

        outcome = await self._status(make_room_binding(room))

        assert not outcome.is_error
        assert outcome.content.splitlines() == [
            "Release crew: 2 agents",
            "- Reviewer, code-reviewer, leads (here); last answer: Two nits.",
            "- Builder, builder (not here); last error: Compile failed",
            "People here: Ada",
        ]

    async def test_an_agent_that_never_posted_has_no_last_line(self):
        room = _team(make_member("Scout", present=True))

        outcome = await self._status(make_room_binding(room))

        assert outcome.content.splitlines() == [
            "Release crew: 1 agents",
            "- Scout (here)",
            "People here: nobody else",
        ]

    async def test_a_status_that_does_not_fit_is_cut_marked_and_counted(self):
        room = _team(*[make_member(f"Agent-{i}", present=True) for i in range(20)])

        outcome = await self._status(make_room_binding(room, full_read_max_chars=200))

        assert len(outcome.content) == 200
        assert outcome.content.endswith("[…cut: the realtime client takes no more]")
        assert cap_hit_counts() == {READ_ROOM_KNOB: 1}


class TestReadRoom:
    async def _read(self, binding):
        return await binding.call_tool(READ_ROOM_TOOL, {}, author=ADA, call_id="c1")

    async def test_an_empty_room_has_no_posts(self):
        outcome = await self._read(make_room_binding(_crew()))

        assert outcome.content == "Release crew has no posts yet."
        assert cap_hit_counts() == {}

    async def test_the_whole_conversation_oldest_first_when_it_fits(self):
        room = _crew()
        room.current_posts = [
            make_post("1", "Ada", kind=PostKind.PERSON, text="Review please"),
            make_post("2", "Reviewer", text="  Done.  "),
        ]

        outcome = await self._read(make_room_binding(room))

        assert outcome.content == (
            "Release crew: the whole conversation, 2 posts, oldest first.\n\n"
            "[Ada, person] Review please\n[Reviewer, answer] Done."
        )
        assert cap_hit_counts() == {}

    async def test_what_does_not_fit_leaves_out_the_oldest_and_is_counted(self):
        room = _crew()
        room.current_posts = [make_post(str(i), "A", text=f"{i}" * 100) for i in range(3)]

        outcome = await self._read(make_room_binding(room, full_read_max_chars=700))

        head, _, body = outcome.content.partition("\n\n")
        assert head == (
            "Release crew: the newest 1 of 3 posts; the 2 earlier posts do not fit in one tool "
            "result. Say so if the person asks about what is left out."
        )
        assert body == "[A, answer] " + "2" * 100
        assert cap_hit_counts() == {READ_ROOM_KNOB: 1}

    async def test_a_newest_post_too_long_for_one_result_is_cut_and_counted(self):
        room = _crew()
        room.current_posts = [make_post("1", "A", text="y" * 400)]

        outcome = await self._read(make_room_binding(room, full_read_max_chars=700))

        head, _, body = outcome.content.partition("\n\n")
        assert head == (
            "Release crew: the newest 1 of 1 posts; the newest is cut to fit. "
            "Say so if the person asks about what is left out."
        )
        assert body.endswith(" […cut: the rest of this post does not fit in one tool result]")
        assert len(body) == 700 - 512
        assert cap_hit_counts() == {READ_ROOM_KNOB: 1}

    async def test_a_cut_newest_post_with_earlier_ones_left_out_says_both(self):
        room = _crew()
        room.current_posts = [
            make_post("1", "A", text="old"),
            make_post("2", "A", text="z" * 400),
        ]

        outcome = await self._read(make_room_binding(room, full_read_max_chars=700))

        head = outcome.content.partition("\n\n")[0]
        assert head == (
            "Release crew: the newest 1 of 2 posts; the newest is cut to fit; the 1 earlier "
            "posts do not fit in one tool result. Say so if the person asks about what is "
            "left out."
        )
        assert cap_hit_counts() == {READ_ROOM_KNOB: 1}


class TestFramesForEveryKind:
    def test_a_person_is_a_silent_note_with_their_own_words(self):
        binding = make_room_binding(_crew())
        post = make_post(
            "p1", "Ada", kind=PostKind.PERSON, text="Heads up\nSpoken: not a spoken line"
        )

        frames = binding.frames_for(post)

        assert frames == [
            (
                ROOM_NOTE,
                {
                    "postId": "p1",
                    "member": "Ada",
                    "persona": "",
                    "kind": "person",
                    "text": "[Ada posted, person] Heads up\nSpoken: not a spoken line",
                    "speak": False,
                },
            )
        ]

    def test_an_agent_with_a_spoken_line_is_a_note_then_a_speak_frame_in_its_voices(self):
        room = _team(
            make_member(
                "Reviewer",
                persona="code-reviewer",
                present=True,
                voices={"grok-voice": "rex", "openai-realtime": "ash"},
            )
        )
        post = make_post(
            "p2",
            "Reviewer",
            persona="code-reviewer",
            text="Full review below.\n\nSpoken: Two small nits, nothing blocking.",
        )

        frames = make_room_binding(room).frames_for(post)

        assert frames == [
            (
                ROOM_NOTE,
                {
                    "postId": "p2",
                    "member": "Reviewer",
                    "persona": "code-reviewer",
                    "kind": "answer",
                    "text": "[Reviewer posted, answer] Full review below.",
                    "speak": False,
                },
            ),
            (
                SPEAK,
                {
                    "postId": "p2",
                    "member": "Reviewer",
                    "persona": "code-reviewer",
                    "kind": "answer",
                    "text": "Two small nits, nothing blocking.",
                    "voices": {"grok-voice": "rex", "openai-realtime": "ash"},
                },
            ),
        ]
        assert cap_hit_counts() == {}

    def test_the_voices_are_a_copy_not_the_members_own_dict(self):
        member = make_member("Reviewer", present=True, voices={"grok-voice": "rex"})
        binding = make_room_binding(_team(member))

        frames = binding.frames_for(make_post(text="Done.\nSpoken: Done."))

        frames[1][1]["voices"]["grok-voice"] = "changed"
        assert member.voices == {"grok-voice": "rex"}

    def test_an_agent_that_is_not_a_known_member_speaks_with_no_voices(self):
        frames = make_room_binding(_crew()).frames_for(
            make_post("p3", "Stranger", text="Hi\nSpoken: Hello there.")
        )

        assert [f[0] for f in frames] == [ROOM_NOTE, SPEAK]
        assert frames[1][1]["voices"] == {}
        assert frames[1][1]["member"] == "Stranger"

    def test_a_spoken_line_at_the_bound_is_spoken(self):
        line = "w" * MAX_SPEAK_TEXT_CHARS

        frames = make_room_binding(_crew()).frames_for(make_post(text=f"Body\nSpoken: {line}"))

        assert [f[0] for f in frames] == [ROOM_NOTE, SPEAK]
        assert frames[1][1]["text"] == line
        assert cap_hit_counts() == {}

    def test_a_spoken_line_over_the_bound_is_never_a_speak_frame_and_is_counted(self, caplog):
        line = "w" * (MAX_SPEAK_TEXT_CHARS + 1)
        post = make_post("p4", "Reviewer", text=f"The long review.\nSpoken: {line}")

        frames = make_room_binding(_crew()).frames_for(post)

        assert [f[0] for f in frames] == [ROOM_NOTE]
        note = frames[0][1]
        assert note["speak"] is True
        assert note["text"] == "[Reviewer posted, answer] The long review."
        assert "Reviewer posted a reply" in note["say"]
        assert line not in note["say"]
        assert cap_hit_counts() == {SPOKEN_LINE_KNOB: 1}
        assert f"has {MAX_SPEAK_TEXT_CHARS + 1} characters" in caplog.text
        assert "keep its Spoken: line short" in caplog.text

    @pytest.mark.parametrize(
        ("kind", "said"),
        [
            (PostKind.ANSWER, "Reviewer posted a reply"),
            (PostKind.QUESTION, "Reviewer posted a question for the person"),
            (PostKind.ERROR, "Reviewer posted that its work failed"),
        ],
    )
    def test_a_speakable_post_without_a_line_is_announced_by_the_host_not_read_out(
        self, kind, said
    ):
        body = "## Findings\n- `a.py` leaks a handle\n- b.py is fine"

        frames = make_room_binding(_crew()).frames_for(
            make_post("p5", "Reviewer", kind=kind, text=body)
        )

        assert [f[0] for f in frames] == [ROOM_NOTE]
        note = frames[0][1]
        assert note["speak"] is True
        assert note["kind"] == kind.value
        assert said in note["say"]
        assert "offer the gist. Do not read it out." in note["say"]
        assert "leaks a handle" not in note["say"]
        assert "read it out" not in note["say"].replace("Do not read it out", "")

    def test_a_long_reply_is_announced_with_its_word_count(self):
        body = " ".join(["word"] * 80)

        note = make_room_binding(_crew()).frames_for(make_post(text=body))[0][1]

        assert "posted a long reply (80 words), and offer the gist" in note["say"]

    def test_a_reply_just_under_the_long_bound_is_a_plain_reply(self):
        body = " ".join(["word"] * 79)

        note = make_room_binding(_crew()).frames_for(make_post(text=body))[0][1]

        assert "posted a reply, and offer" in note["say"]

    def test_a_notice_is_a_silent_note(self):
        post = make_post("p6", "room", kind=PostKind.NOTICE, text="Review step started")

        frames = make_room_binding(_crew()).frames_for(post)

        assert frames == [
            (
                ROOM_NOTE,
                {
                    "postId": "p6",
                    "member": "room",
                    "persona": "",
                    "kind": "notice",
                    "text": "[room posted, notice] Review step started",
                    "speak": False,
                },
            )
        ]

    def test_a_notice_is_never_spoken_even_when_it_ends_with_a_spoken_line(self):
        post = make_post("p7", "Builder", kind=PostKind.NOTICE, text="Handed off.\nSpoken: Over.")

        frames = make_room_binding(_crew()).frames_for(post)

        assert [f[0] for f in frames] == [ROOM_NOTE]
        assert frames[0][1]["speak"] is False
        assert "say" not in frames[0][1]
        assert frames[0][1]["text"] == "[Builder posted, notice] Handed off."

    def test_a_note_longer_than_the_clip_is_cut_and_counted(self):
        frames = make_room_binding(_crew(), card_turn_max_chars=20).frames_for(
            make_post(text="q" * 50, kind=PostKind.NOTICE)
        )

        assert frames[0][1]["text"].endswith(f" […30 more characters; {READ_ROOM_TOOL}]")
        assert cap_hit_counts() == {CARD_TURN_KNOB: 1}


class TestCallPresenceGatesPushes:
    def test_attach_listens_to_the_room(self):
        room = _crew()
        binding = make_room_binding(room)

        binding.attach(FakeOutlet())

        assert room.listener == binding._on_post

    async def test_nobody_on_the_call_means_nothing_is_pushed(self):
        room = _crew()
        outlet = FakeOutlet()
        binding = make_room_binding(room)
        binding.attach(outlet)

        room.listener(make_post())
        await _settle(binding)

        assert outlet.frames == []
        assert binding._pushes == set()

    async def test_without_an_outlet_a_post_is_dropped_without_a_task(self):
        binding = make_room_binding(_crew())
        await binding.call_started(ADA)

        binding._on_post(make_post())

        assert binding._pushes == set()

    async def test_a_push_without_an_outlet_sends_nothing(self):
        binding = make_room_binding(_crew())

        await binding._push_post(make_post())

    async def test_a_person_on_the_call_gets_the_posts(self):
        room = _crew()
        outlet = FakeOutlet()
        binding = make_room_binding(room)
        binding.attach(outlet)
        await binding.call_started(ADA)

        room.listener(make_post("p1", text="Done."))
        await _settle(binding)

        assert [(t, p["postId"]) for t, p in outlet.frames] == [(ROOM_NOTE, "p1")]
        assert binding._pushes == set()

    async def test_a_call_without_a_known_author_counts_as_a_call(self):
        binding = make_room_binding(_crew())
        assert binding.on_call is False

        await binding.call_started(None)
        assert binding.on_call is True

        await binding.call_ended(None)
        assert binding.on_call is False

    async def test_ending_a_call_that_never_started_leaves_the_count_at_zero(self):
        binding = make_room_binding(_crew())

        await binding.call_ended(None)
        await binding.call_started(None)

        assert binding.on_call is True

    async def test_the_call_ends_for_everyone_only_when_the_last_phone_hangs_up(self):
        bob = MessageAuthor(user_id="bob", display_name="Bob")
        binding = make_room_binding(_crew())

        await binding.call_started(ADA)
        await binding.call_started(bob)
        await binding.call_ended(ADA)
        assert binding.on_call is True

        await binding.call_ended(bob)
        assert binding.on_call is False

    async def test_posts_stop_after_the_call_ends(self):
        room = _crew()
        outlet = FakeOutlet()
        binding = make_room_binding(room)
        binding.attach(outlet)
        await binding.call_started(ADA)
        await binding.call_ended(ADA)

        room.listener(make_post())
        await _settle(binding)

        assert outlet.frames == []


class TestPostsArriveInOrder:
    async def test_a_notes_frame_comes_before_its_speak_frame(self):
        room = _crew()
        outlet = FakeOutlet(yields=3)
        binding = make_room_binding(room)
        binding.attach(outlet)
        await binding.call_started(ADA)

        room.listener(make_post("p1", text="Review.\nSpoken: Two nits."))
        await _settle(binding)

        assert [t for t, _ in outlet.frames] == [ROOM_NOTE, SPEAK]

    async def test_two_posts_back_to_back_are_pushed_post_one_before_post_two(self):
        room = _crew()
        outlet = FakeOutlet(yields=3)
        binding = make_room_binding(room)
        binding.attach(outlet)
        await binding.call_started(ADA)

        room.listener(make_post("p1", text="Review.\nSpoken: Two nits."))
        room.listener(make_post("p2", "Builder", text="Built.\nSpoken: Built it."))
        room.listener(make_post("p3", "Builder", kind=PostKind.NOTICE, text="Handed off"))
        await _settle(binding)

        assert [(t, p["postId"]) for t, p in outlet.frames] == [
            (ROOM_NOTE, "p1"),
            (SPEAK, "p1"),
            (ROOM_NOTE, "p2"),
            (SPEAK, "p2"),
            (ROOM_NOTE, "p3"),
        ]

    async def test_a_long_spoken_line_is_never_pushed_as_a_speak_frame(self):
        room = _crew()
        outlet = FakeOutlet()
        binding = make_room_binding(room)
        binding.attach(outlet)
        await binding.call_started(ADA)

        room.listener(make_post("p1", text="Review.\nSpoken: " + "w" * (MAX_SPEAK_TEXT_CHARS + 1)))
        await _settle(binding)

        assert [t for t, _ in outlet.frames] == [ROOM_NOTE]
        assert outlet.frames[0][1]["speak"] is True
        assert cap_hit_counts() == {SPOKEN_LINE_KNOB: 1}


class TestStatusIsReadLive:
    def test_the_roster_is_read_each_time_not_cached(self):
        room = _team(make_member("Scout", present=False))
        binding = make_room_binding(room)
        assert "Scout (not here)" in binding.grounding()

        room.members = (make_member("Scout", present=True),)

        assert "Scout (here)" in binding.grounding()


def test_the_status_of_a_room_says_who_it_speaks_to():
    team = RoomVoiceStatus(room_id="r", name="n", kind=RoomKind.TEAM)
    run = RoomVoiceStatus(room_id="r", name="n", kind=RoomKind.RUN)

    assert team.speaks_to_everyone is True
    assert run.speaks_to_everyone is False


def test_a_post_defaults_have_no_persona_or_time():
    post = RoomPost(post_id="1", speaker="A", kind=PostKind.ANSWER, text="x")

    assert (post.persona, post.created_at) == ("", "")


class TestRoomContext:
    def test_persona_get_carries_the_room_its_agents_and_their_voices(self):
        room = FakeRoom(
            members=(
                RoomMember(
                    name="Reviewer",
                    persona="reviewer",
                    lead=True,
                    present=True,
                    voices={"grok-voice": "rex"},
                ),
                RoomMember(name="Coder", persona="coder"),
            ),
            people=("Ada",),
        )

        context = make_room_binding(room).context()

        assert context == {
            "room": {
                "id": room.status().room_id,
                "name": "Release crew",
                "kind": "team",
                "speaksToEveryone": True,
                "members": [
                    {
                        "name": "Reviewer",
                        "persona": "reviewer",
                        "lead": True,
                        "present": True,
                        "voices": {"grok-voice": "rex"},
                    },
                    {
                        "name": "Coder",
                        "persona": "coder",
                        "lead": False,
                        "present": False,
                        "voices": {},
                    },
                ],
                "people": ["Ada"],
            }
        }

    def test_a_run_room_does_not_speak_to_everyone(self):
        context = make_room_binding(FakeRoom(kind=RoomKind.RUN)).context()

        assert context["room"]["kind"] == "run"
        assert context["room"]["speaksToEveryone"] is False
