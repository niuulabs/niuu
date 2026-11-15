"""``ask_person``: the agent asks one named person of its project and gets the answer back.

The question is a durable turn in the room addressed to the person; the answer is a peer
message from the person who answered (the addressee, or an approver), in reply to the question,
so it is taken by the waiting call, or delivered at the next turn boundary like any late
answer. The broker, its inbox and its durable log are real; Forge's REST is faked.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from niuu import cap_telemetry
from niuu.domain.message_author import MessageAuthor
from niuu.domain.peer_message import PeerKind
from niuu.domain.person_question import (
    PERSON_ANSWER_MESSAGE,
    PERSON_ANSWERED_FRAME,
    person_answer_id,
    person_question_id,
)
from niuu.forge_mcp.models import ForgeApiError
from skuld import peer_inbox
from skuld.broker import Broker
from skuld.channels import WebSocketChannel
from skuld.forge_mcp import host as host_module
from skuld.forge_mcp.host import ASK_OUTCOMES_COUNTER, BrokerForgeMcpHost
from skuld.person_questions import PersonQuestion, person_answer_turn_id
from tests.test_skuld.test_peer_inbox import SESSION_ID, _inbox_broker, _sent_texts, _settle

ALICE = MessageAuthor(user_id="u-alice", display_name="Alice Liddell")
BOB = MessageAuthor(user_id="u-bob", display_name="Bob Stone")
CAROL = MessageAuthor(user_id="u-carol", display_name="Carol Ng")
ADDRESSEE = {"user_id": "u-alice", "display_name": "Alice Liddell", "role": "viewer"}
QUESTION_ID = person_question_id(SESSION_ID, "u-alice", "req-1")
QUESTION = "Which API key do we use for staging?"


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_json(self, frame: dict) -> None:
        self.sent.append(frame)

    async def close(self) -> None:
        return None


class ForgeRecorder:
    """The broker's HTTP client: records the settle calls and can be made to fail them."""

    def __init__(self, base) -> None:
        self.base = base
        self.requests: list[dict] = []
        self.failure: int | None = None

    async def post(self, *args, **kwargs):
        return await self.base.post(*args, **kwargs)

    async def request(self, method, url, *, json=None, **_kwargs):
        self.requests.append({"method": method, "url": str(url), "json": json})
        if self.failure is not None:
            return httpx.Response(self.failure, json={"detail": "Forge is down"})
        return httpx.Response(200, json={"question_id": QUESTION_ID, "resolved": True})


@pytest.fixture
def outcomes(monkeypatch):
    calls: list[dict] = []
    telemetry = SimpleNamespace(
        count=lambda name, value=1, *, attributes=None, description="": calls.append(
            {"name": name, **(attributes or {})}
        )
    )
    monkeypatch.setattr(host_module, "get_observability", lambda: telemetry)
    monkeypatch.setattr(peer_inbox, "get_observability", lambda: telemetry)
    cap_telemetry.reset_cap_hits()
    return calls


def _world(tmp_path, *, activity: str = "active", **ask: Any):
    broker = _inbox_broker(tmp_path, activity=activity)
    broker._settings.ask = broker._settings.ask.model_copy(
        update={"poll_interval_s": 0.01, "wait_max_s": 5.0, **ask}
    )
    forge = ForgeRecorder(broker._http_client)
    broker._http_client = forge
    return BrokerForgeMcpHost(broker, SimpleNamespace()), broker, forge


def _connect(broker, author: MessageAuthor | None, role: str = "viewer") -> FakeSocket:
    ws = FakeSocket()
    broker._channels.add(WebSocketChannel(ws, room_role=role, author=author))
    return ws


async def _ask(host: BrokerForgeMcpHost, *, deadline_s: float = 30, question_id=QUESTION_ID):
    return await host.ask_person_answer(
        addressee=ADDRESSEE,
        question=QUESTION,
        question_id=question_id,
        request_id="req-1",
        deadline_s=deadline_s,
    )


async def _answer(broker, ws, content="Use the staging key", question_id=QUESTION_ID):
    await broker._dispatch_browser_message(
        {"type": PERSON_ANSWER_MESSAGE, "question_id": question_id, "content": content},
        sender_ws=ws,
    )


def _errors(ws: FakeSocket) -> list[dict]:
    return [frame for frame in ws.sent if frame.get("type") == "error"]


def _frames(broker, kind: str) -> list[dict]:
    return [e["payload"] for e in broker._event_log_buffer if e["payload"].get("type") == kind]


class TestTheQuestionInTheRoom:
    async def test_it_is_a_durable_turn_addressed_to_the_person(self, tmp_path, outcomes) -> None:
        host, broker, _ = _world(tmp_path)
        watcher = _connect(broker, BOB)
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)

        [turn] = [t for t in broker._conversation_turns if t.id == f"pq_{QUESTION_ID}"]
        assert turn.content == f"Asked Alice Liddell: {QUESTION}"
        assert turn.parts[0]["name"] == "ask_person"
        assert turn.parts[0]["input"]["user_id"] == "u-alice"
        assert turn.parts[0]["input"]["question"] == QUESTION
        # Durable first, then live to everyone in the room.
        assert [f for f in _frames(broker, "conversation.turn") if f["turn"]["id"] == turn.id]
        assert any(m.get("turn", {}).get("id") == turn.id for m in watcher.sent)
        asking.cancel()
        await asyncio.gather(asking, return_exceptions=True)

    async def test_asking_again_for_the_same_question_does_not_post_it_twice(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, _ = _world(tmp_path)
        assert (await _ask(host, deadline_s=0.05))["outcome"] == "deadline"
        assert (await _ask(host, deadline_s=0.05))["outcome"] == "deadline"
        assert len([t for t in broker._conversation_turns if t.id.startswith("pq_")]) == 1

    async def test_the_asked_person_is_found_from_the_conversation_alone(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, _ = _world(tmp_path)
        await _ask(host, deadline_s=0.05)
        question = broker.person_question(QUESTION_ID)
        assert (question.addressee_id, question.addressee_name) == ("u-alice", "Alice Liddell")
        assert question.question == QUESTION
        assert broker.person_question("unknown") is None

    @pytest.mark.parametrize("setting", ["event_log_enabled", "peer_inbox"])
    async def test_a_session_that_could_not_get_the_answer_refuses_loudly(
        self, tmp_path, setting
    ) -> None:
        host, broker, _ = _world(tmp_path)
        if setting == "peer_inbox":
            broker._settings.peer_inbox.enabled = False
        else:
            broker._settings.event_log_enabled = False
        with pytest.raises(ForgeApiError) as raised:
            await _ask(host, deadline_s=0.05)
        assert raised.value.status == 503
        assert not [t for t in broker._conversation_turns if t.id.startswith("pq_")]


class TestTheAnswerWhileWaiting:
    async def test_the_addressees_answer_is_the_result_with_its_author(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, forge = _world(tmp_path, activity="idle")
        alice = _connect(broker, ALICE)
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)

        await _answer(broker, alice)
        result = await asyncio.wait_for(asking, 2)
        await _settle()

        assert result["outcome"] == "answered"
        assert result["answer"] == "Use the staging key"
        assert result["author"] == {"user_id": "u-alice", "display_name": "Alice Liddell"}
        assert result["answered_by_the_person_asked"] is True
        assert result["from"] == "Alice Liddell (person u-alice)"
        assert _errors(alice) == []
        # Consumed by the call: the harness is not handed it again.
        assert _sent_texts(broker) == []
        assert broker._peer_messages_waiting() is False
        assert [c["outcome"] for c in outcomes if c["name"] == ASK_OUTCOMES_COUNTER] == ["answered"]

    async def test_the_answer_is_recorded_with_who_gave_it(self, tmp_path, outcomes) -> None:
        host, broker, forge = _world(tmp_path)
        alice = _connect(broker, ALICE)
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)

        await _answer(broker, alice)
        await asyncio.wait_for(asking, 2)

        [frame] = _frames(broker, PERSON_ANSWERED_FRAME)
        assert frame["author"] == {"user_id": "u-alice", "display_name": "Alice Liddell"}
        assert frame["question_id"] == QUESTION_ID and frame["content"] == "Use the staging key"
        assert frame["addressee"]["user_id"] == "u-alice"
        stored = broker._peer_inbox[person_answer_id(SESSION_ID, QUESTION_ID)].envelope
        assert (stored.sender.kind, stored.sender.id, stored.in_reply_to) == (
            PeerKind.PERSON,
            "u-alice",
            QUESTION_ID,
        )

    async def test_a_browser_that_attaches_later_is_told_the_answer(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, _ = _world(tmp_path)
        alice = _connect(broker, ALICE)
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)
        assert broker.answered_person_question_frames() == []

        await _answer(broker, alice)
        await asyncio.wait_for(asking, 2)

        [replayed] = broker.answered_person_question_frames()
        [recorded] = _frames(broker, PERSON_ANSWERED_FRAME)
        assert replayed == recorded

    async def test_forge_is_told_who_answered_so_the_notification_settles(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, forge = _world(tmp_path)
        alice = _connect(broker, ALICE)
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)
        await _answer(broker, alice)
        await asyncio.wait_for(asking, 2)

        [request] = forge.requests
        assert request["method"] == "POST"
        assert request["url"].endswith(
            f"/sessions/{SESSION_ID}/person-questions/{QUESTION_ID}/answer"
        )
        assert request["json"] == {
            "answered_by_user_id": "u-alice",
            "answered_by_name": "Alice Liddell",
        }

    async def test_an_approver_may_answer_for_the_person_asked(self, tmp_path, outcomes) -> None:
        host, broker, forge = _world(tmp_path)
        bob = _connect(broker, BOB, role="approver")
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)

        await _answer(broker, bob, "Alice is away: use the staging key")
        result = await asyncio.wait_for(asking, 2)

        assert result["outcome"] == "answered"
        assert result["author"]["user_id"] == "u-bob"
        assert result["answered_by_the_person_asked"] is False
        assert forge.requests[0]["json"]["answered_by_user_id"] == "u-bob"

    async def test_the_owner_may_answer_too(self, tmp_path, outcomes) -> None:
        host, broker, _ = _world(tmp_path)
        owner = _connect(broker, BOB, role="owner")
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)
        await _answer(broker, owner)
        assert (await asyncio.wait_for(asking, 2))["author"]["user_id"] == "u-bob"


class TestWhoMayAnswer:
    async def _open_question(self, tmp_path):
        host, broker, forge = _world(tmp_path)
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)
        return host, broker, forge, asking

    async def _close(self, asking) -> None:
        asking.cancel()
        await asyncio.gather(asking, return_exceptions=True)

    async def test_someone_else_without_the_approver_role_is_refused(
        self, tmp_path, outcomes
    ) -> None:
        _, broker, forge, asking = await self._open_question(tmp_path)
        carol = _connect(broker, CAROL, role="viewer")

        await _answer(broker, carol)

        [error] = _errors(carol)
        assert error["code"] == "forbidden" and "Alice Liddell" in error["content"]
        assert "approver" in error["content"]
        assert forge.requests == [] and not asking.done()
        assert person_answer_id(SESSION_ID, QUESTION_ID) not in broker._peer_inbox
        await self._close(asking)

    async def test_a_connection_with_no_verified_identity_cannot_answer(
        self, tmp_path, outcomes
    ) -> None:
        _, broker, forge, asking = await self._open_question(tmp_path)
        anonymous = _connect(broker, None, role="owner")

        await _answer(broker, anonymous)

        [error] = _errors(anonymous)
        assert error["code"] == "forbidden" and "not verified" in error["content"]
        assert forge.requests == [] and not asking.done()
        await self._close(asking)

    async def test_the_author_is_the_connections_never_the_messages(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, _, asking = await self._open_question(tmp_path)
        carol = _connect(broker, CAROL, role="viewer")
        await broker._dispatch_browser_message(
            {
                "type": PERSON_ANSWER_MESSAGE,
                "question_id": QUESTION_ID,
                "content": "I am Alice",
                "author": {"user_id": "u-alice", "display_name": "Alice Liddell"},
                "user_id": "u-alice",
            },
            sender_ws=carol,
        )
        assert _errors(carol)[0]["code"] == "forbidden"
        assert not asking.done()
        await self._close(asking)

    async def test_an_unknown_question_and_a_blank_answer_are_refused(
        self, tmp_path, outcomes
    ) -> None:
        _, broker, forge, asking = await self._open_question(tmp_path)
        alice = _connect(broker, ALICE)

        await _answer(broker, alice, question_id="not-a-question")
        await _answer(broker, alice, content="   ")

        assert [e["code"] for e in _errors(alice)] == ["not_found", "invalid"]
        assert forge.requests == [] and not asking.done()
        await self._close(asking)

    async def test_a_question_takes_one_answer(self, tmp_path, outcomes) -> None:
        host, broker, forge, asking = await self._open_question(tmp_path)
        alice = _connect(broker, ALICE)
        bob = _connect(broker, BOB, role="approver")
        await _answer(broker, alice)
        await asyncio.wait_for(asking, 2)

        await _answer(broker, bob, "A second answer")

        [error] = _errors(bob)
        assert (
            error["code"] == "conflict" and "already answered by Alice Liddell" in error["content"]
        )
        assert len(forge.requests) == 1
        assert len(_frames(broker, PERSON_ANSWERED_FRAME)) == 1

    async def test_a_synthesized_dispatch_with_no_connection_is_refused(
        self, tmp_path, outcomes
    ) -> None:
        _, broker, _, asking = await self._open_question(tmp_path)
        with pytest.raises(ValueError, match="not verified"):
            await _answer(broker, None)
        await self._close(asking)


class TestWhenForgeCannotRecordIt:
    async def test_the_answer_is_refused_and_the_question_stays_open_for_a_retry(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, forge = _world(tmp_path)
        alice = _connect(broker, ALICE)
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)
        forge.failure = 503

        await _answer(broker, alice)

        [error] = _errors(alice)
        assert error["code"] == "forge_unavailable" and "Try again" in error["content"]
        assert person_answer_id(SESSION_ID, QUESTION_ID) not in broker._peer_inbox
        assert _frames(broker, PERSON_ANSWERED_FRAME) == [] and not asking.done()

        forge.failure = None
        await _answer(broker, alice)
        assert (await asyncio.wait_for(asking, 2))["outcome"] == "answered"


class TestWhenTheWaitEndsFirst:
    async def test_a_deadline_is_a_named_outcome_never_an_empty_answer(
        self, tmp_path, outcomes
    ) -> None:
        host, _, _ = _world(tmp_path)

        result = await _ask(host, deadline_s=0.05)

        assert result["outcome"] == "deadline" and "answer" not in result
        assert "Alice Liddell" in result["detail"] and "req-1" in result["detail"]
        assert "next turn boundary" in result["detail"]
        assert [c["outcome"] for c in outcomes if c["name"] == ASK_OUTCOMES_COUNTER] == ["deadline"]

    async def test_a_wait_at_the_ceiling_is_counted_as_a_cap_hit(self, tmp_path, outcomes) -> None:
        host, _, _ = _world(tmp_path, wait_max_s=0.05)
        await _ask(host, deadline_s=0.05)
        assert "ask.wait_max_s" in cap_telemetry.cap_hit_counts()
        cap_telemetry.reset_cap_hits()
        host, _, _ = _world(tmp_path, wait_max_s=5.0)
        await _ask(host, deadline_s=0.05)
        assert cap_telemetry.cap_hit_counts() == {}

    async def test_a_late_answer_is_alices_own_message_handed_over_at_the_boundary(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, forge = _world(tmp_path, activity="idle")
        alice = _connect(broker, ALICE)
        assert (await _ask(host, deadline_s=0.05))["outcome"] == "deadline"

        await _answer(broker, alice, "Use the staging key")
        await _settle()

        # The agent reads it as a person's message, told which question it answers; no
        # machine envelope.
        [text] = _sent_texts(broker)
        assert text.endswith("Use the staging key")
        assert "Alice Liddell is answering the question you asked with ask_person" in text
        assert QUESTION_ID in text and QUESTION in text
        assert "[Message " not in text and "(person u-alice)" not in text
        # The conversation shows it once, as Alice's message.
        answers = [t for t in broker._conversation_turns if t.role == "user"]
        assert [(t.id, t.content) for t in answers] == [
            (person_answer_turn_id(QUESTION_ID), "Use the staging key")
        ]
        assert answers[0].author == {"user_id": "u-alice", "display_name": "Alice Liddell"}
        assert forge.requests[0]["json"]["answered_by_user_id"] == "u-alice"
        assert broker._peer_inbox[person_answer_id(SESSION_ID, QUESTION_ID)].injected is True

    async def test_an_answer_taken_by_the_waiting_call_is_shown_once_and_not_sent_again(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, _ = _world(tmp_path, activity="idle")
        alice = _connect(broker, ALICE)
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)

        await _answer(broker, alice, "Use the staging key")
        await asyncio.wait_for(asking, 2)
        await _settle()

        assert _sent_texts(broker) == []
        [turn] = [t for t in broker._conversation_turns if t.role == "user"]
        assert turn.id == person_answer_turn_id(QUESTION_ID)
        assert turn.metadata["steering_state"] == "active"
        confirmed = [f for f in alice.sent if f.get("type") == "user_confirmed"]
        assert [f["id"] for f in confirmed] == [person_answer_turn_id(QUESTION_ID)]

    async def test_an_approver_answering_for_the_person_is_named_as_such(self) -> None:
        question = PersonQuestion(
            question_id=QUESTION_ID,
            addressee_id="u-alice",
            addressee_name="Alice Liddell",
            question=QUESTION,
        )
        note = Broker._person_answer_note(question, MessageAuthor("u-bob", "Bob"))
        assert note.startswith("[Bob is answering on behalf of Alice Liddell the question")
        assert Broker._person_answer_note(None, MessageAuthor("u-bob", "Bob")).startswith(
            "[Bob is answering a question"
        )

    async def test_a_late_answer_waits_for_the_boundary_while_a_turn_runs(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, _ = _world(tmp_path, activity="active")
        alice = _connect(broker, ALICE)
        await _ask(host, deadline_s=0.05)
        await _answer(broker, alice)
        await _settle()
        assert _sent_texts(broker) == []

        broker._activity_state = "idle"
        await broker._inject_at_turn_boundary()
        await _settle()
        assert len(_sent_texts(broker)) == 1

    async def test_asking_again_after_a_late_answer_returns_it_once_noting_the_repeat(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, _ = _world(tmp_path, activity="idle")
        alice = _connect(broker, ALICE)
        await _ask(host, deadline_s=0.05)
        await _answer(broker, alice)
        await _settle()
        assert len(_sent_texts(broker)) == 1

        result = await _ask(host, deadline_s=0.05)

        assert result["outcome"] == "answered" and result["answer"] == "Use the staging key"
        assert "twice" in result["note"]
        assert len(_sent_texts(broker)) == 1  # not handed over again

    async def test_asking_again_keeps_waiting_and_takes_the_answer_when_it_comes(
        self, tmp_path, outcomes
    ) -> None:
        host, broker, _ = _world(tmp_path, activity="idle")
        alice = _connect(broker, ALICE)
        assert (await _ask(host, deadline_s=0.05))["outcome"] == "deadline"
        asking = asyncio.create_task(_ask(host))
        await asyncio.sleep(0.05)

        await _answer(broker, alice)
        result = await asyncio.wait_for(asking, 2)
        await _settle()

        assert result["outcome"] == "answered" and "note" not in result
        assert _sent_texts(broker) == []


class TestTheDeciderOfAPermissionOrAQuestion:
    @pytest.fixture
    def decided(self, tmp_path):
        _, broker, _ = _world(tmp_path)
        broker._transport.send_control_response = _async_noop()
        broker._transport.send_control = _async_noop()
        broker._pending_permission_requests["perm-1"] = {"request_id": "perm-1"}
        return broker

    async def test_a_permission_response_names_the_person_who_decided(self, decided) -> None:
        bob = _connect(decided, BOB, role="approver")

        await decided._dispatch_browser_message(
            {"type": "permission_response", "request_id": "perm-1", "behavior": "allow"},
            sender_ws=bob,
        )

        [frame] = _frames(decided, "permission_resolved")
        assert frame["behavior"] == "allow" and frame["auto_approved"] is False
        assert frame["decided_by"] == {"user_id": "u-bob", "display_name": "Bob Stone"}

    async def test_a_decision_without_an_identity_names_nobody(self, decided) -> None:
        anonymous = _connect(decided, None, role="approver")
        await decided._dispatch_browser_message(
            {"type": "permission_response", "request_id": "perm-1", "behavior": "deny"},
            sender_ws=anonymous,
        )
        [frame] = _frames(decided, "permission_resolved")
        assert "decided_by" not in frame

    async def test_an_auto_approval_names_nobody(self, decided) -> None:
        await decided._send_permission_control_response(
            "perm-1", {"behavior": "allow"}, auto_approved=True
        )
        [frame] = _frames(decided, "permission_resolved")
        assert frame["auto_approved"] is True and "decided_by" not in frame

    async def test_an_answer_to_the_agents_own_question_names_the_person(self, decided) -> None:
        bob = _connect(decided, BOB, role="approver")
        await decided._dispatch_browser_message(
            {"type": "ask_user_answer", "request_id": "ask-1", "answers": [{"id": "a"}]},
            sender_ws=bob,
        )
        [frame] = _frames(decided, "ask_user_answered")
        assert frame["request_id"] == "ask-1"
        assert frame["decided_by"] == {"user_id": "u-bob", "display_name": "Bob Stone"}

    async def test_an_answer_with_no_identity_records_no_decider_frame(self, decided) -> None:
        anonymous = _connect(decided, None, role="approver")
        await decided._dispatch_browser_message(
            {"type": "ask_user_answer", "request_id": "ask-2", "answers": []},
            sender_ws=anonymous,
        )
        assert _frames(decided, "ask_user_answered") == []


def _async_noop():
    async def noop(*args, **kwargs):
        return True

    return noop
