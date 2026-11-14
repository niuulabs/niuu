"""The voice call's tools, which act on the one session it is bound to."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.message_author import MessageAuthor
from skuld.voice.session_port import SendOutcome, SendStatus, SessionTurn
from skuld.voice.tools import (
    FULL_READ_KNOB,
    READ_TOOL,
    SEND_TOOL,
    STATUS_TOOL,
    SessionVoiceTools,
    UnknownToolError,
)
from tests.test_skuld.voice_support import FakeSession, make_status

AUTHOR = MessageAuthor(user_id="owner-1", display_name="Ada")


@pytest.fixture(autouse=True)
def _fresh_cap_counts():
    reset_cap_hits()
    yield
    reset_cap_hits()


def _tools(session: FakeSession, *, max_chars: int = 4096) -> SessionVoiceTools:
    return SessionVoiceTools(session, tool_timeout_seconds=45.5, full_read_max_chars=max_chars)


def test_definitions_describe_the_three_tools():
    definitions = _tools(FakeSession()).definitions()

    assert [d["name"] for d in definitions] == [SEND_TOOL, STATUS_TOOL, READ_TOOL]
    assert all(d["category"] == "session" and d["timeoutMs"] == 45_500 for d in definitions)
    send, status, read = definitions
    assert send["idempotent"] is False
    assert send["parameters"]["required"] == ["message"]
    assert status["idempotent"] is True and read["idempotent"] is True
    assert status["parameters"]["properties"] == {}


def test_names():
    assert _tools(FakeSession()).names == [SEND_TOOL, STATUS_TOOL, READ_TOOL]


class TestSend:
    async def test_delivered(self):
        session = FakeSession()
        tools = _tools(session)

        outcome = await tools.call(
            SEND_TOOL, {"message": "  run the tests  "}, author=AUTHOR, call_id="C1"
        )

        assert not outcome.is_error
        assert outcome.content.startswith("Delivered: Refactor billing received the message")
        assert "open question" not in outcome.content
        assert session.sent == [("run the tests", AUTHOR, "C1")]

    async def test_delivered_while_the_session_waits_on_questions(self):
        session = FakeSession()
        session.outcome = SendOutcome(SendStatus.DELIVERED, pending_questions=2)

        outcome = await _tools(session).call(
            SEND_TOOL, {"message": "go"}, author=AUTHOR, call_id="C1"
        )

        assert "waiting for the person to answer 2 open question(s)" in outcome.content
        assert not outcome.is_error

    async def test_queued_says_it_is_not_yet_acted_on(self):
        session = FakeSession()
        session.outcome = SendOutcome(SendStatus.QUEUED)

        outcome = await _tools(session).call(
            SEND_TOOL, {"message": "go"}, author=AUTHOR, call_id="C1"
        )

        assert outcome.content.startswith("Queued:")
        assert "not yet acted on" in outcome.content
        assert not outcome.is_error

    async def test_failed_is_an_error_with_the_reason(self):
        session = FakeSession()
        session.outcome = SendOutcome(SendStatus.FAILED, "the session did not accept it")

        outcome = await _tools(session).call(
            SEND_TOOL, {"message": "go"}, author=None, call_id="C1"
        )

        assert outcome.is_error
        assert outcome.content == (
            "Not sent: the session did not accept it. Tell the person plainly; "
            "do not say it was sent."
        )

    @pytest.mark.parametrize("args", [{}, {"message": ""}, {"message": "   "}, {"message": None}])
    async def test_an_empty_message_is_an_error_and_is_not_sent(self, args):
        session = FakeSession()

        outcome = await _tools(session).call(SEND_TOOL, args, author=AUTHOR, call_id="C1")

        assert outcome.is_error
        assert "message was empty" in outcome.content
        assert session.sent == []


class TestStatus:
    def _call(self, session: FakeSession, max_chars: int = 4096):
        return _tools(session, max_chars=max_chars).call(STATUS_TOOL, {}, author=None, call_id="")

    async def test_idle_session_with_no_turns(self):
        outcome = await self._call(FakeSession())

        assert outcome.content == "Refactor billing: idle"
        assert not outcome.is_error

    async def test_busy_session_since_when_questions_and_last_turns(self):
        now = datetime.now(UTC).timestamp()
        session = FakeSession(
            status=make_status(
                activity_state="awaiting_input",
                busy=True,
                state_since=now - 300,
                questions=("Which database?", "Which region?"),
            )
        )
        session.current_turns = [
            SessionTurn("user", "first ask"),
            SessionTurn("assistant", "first answer"),
            SessionTurn("user", "second ask"),
            SessionTurn("assistant", "second answer"),
        ]

        outcome = await self._call(session)

        assert outcome.content.split("\n") == [
            "Refactor billing: working for 5 min",
            "asking the person: Which database?",
            "asking the person: Which region?",
            "last message to it: second ask",
            "latest answer: second answer",
        ]

    async def test_idle_state_reads_without_underscores(self):
        session = FakeSession(status=make_status(activity_state="tool_executing"))

        assert (await self._call(session)).content == "Refactor billing: tool executing"

    @pytest.mark.parametrize(
        ("age_seconds", "expected"),
        [(30, "30 s"), (90, "1 min"), (2 * 3600 + 5 * 60 + 3, "2 h 5 min"), (-500, "0 s")],
    )
    async def test_elapsed_time_formats(self, age_seconds, expected):
        since = datetime.now(UTC).timestamp() - age_seconds
        session = FakeSession(status=make_status(state_since=since))

        outcome = await self._call(session)

        assert outcome.content == f"Refactor billing: idle for {expected}"

    async def test_a_status_longer_than_a_tool_result_is_cut_marked_and_counted(self):
        session = FakeSession()
        session.current_turns = [SessionTurn("assistant", "a" * 3000)]

        outcome = await self._call(session, max_chars=1024)

        assert len(outcome.content) == 1024
        assert outcome.content.endswith("[…cut: the realtime client takes no more]")
        assert cap_hit_counts() == {FULL_READ_KNOB: 1}


class TestFullRead:
    def _call(self, session: FakeSession, max_chars: int = 4096):
        return _tools(session, max_chars=max_chars).call(READ_TOOL, {}, author=None, call_id="")

    async def test_no_turns(self):
        outcome = await self._call(FakeSession())

        assert outcome.content == "Refactor billing has no turns yet."

    async def test_a_conversation_that_fits_is_read_whole(self):
        session = FakeSession()
        session.current_turns = [
            SessionTurn("user", "hello", author="Ada"),
            SessionTurn("assistant", "hi"),
            SessionTurn("user", "bye"),
        ]

        outcome = await self._call(session)

        assert outcome.content == (
            "Refactor billing: the whole conversation, 3 turns, oldest first.\n\n"
            "[Ada] hello\n[agent] hi\n[person] bye"
        )
        assert cap_hit_counts() == {}

    async def test_one_that_is_too_long_keeps_the_newest_says_so_and_is_counted(self):
        session = FakeSession()
        session.current_turns = [SessionTurn("user", f"{n}" * 80) for n in range(5)]

        outcome = await self._call(session, max_chars=700)

        assert outcome.content.startswith(
            "Refactor billing: the newest 2 of 5 turns; the 3 earlier turns do not fit in one "
            "tool result. Say so if the person asks about what is left out."
        )
        assert "4" * 80 in outcome.content and "3" * 80 in outcome.content
        assert "2" * 80 not in outcome.content
        assert not outcome.is_error
        assert cap_hit_counts() == {FULL_READ_KNOB: 1}

    async def test_a_single_newest_turn_larger_than_the_budget_is_cut_and_counted(self):
        session = FakeSession()
        session.current_turns = [SessionTurn("assistant", "z" * 5000)]

        outcome = await self._call(session, max_chars=1024)

        assert outcome.content.startswith(
            "Refactor billing: the newest 1 of 1 turns; the newest is cut to fit."
        )
        assert "[…cut: the rest of this turn does not fit in one tool result]" in outcome.content
        assert len(outcome.content) < 1024 + 200
        assert cap_hit_counts() == {FULL_READ_KNOB: 1}

    async def test_an_oversized_newest_turn_also_reports_the_earlier_ones_left_out(self):
        session = FakeSession()
        session.current_turns = [SessionTurn("user", "old"), SessionTurn("assistant", "z" * 5000)]

        outcome = await self._call(session, max_chars=1024)

        assert "the newest is cut to fit; the 1 earlier turns do not fit" in outcome.content
        assert "[person] old" not in outcome.content
        assert cap_hit_counts() == {FULL_READ_KNOB: 1}


async def test_an_unknown_tool_raises():
    with pytest.raises(UnknownToolError, match="launch_missiles"):
        await _tools(FakeSession()).call("launch_missiles", {}, author=None, call_id="")
