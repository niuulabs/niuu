"""Questions asked by publishing: many answers or none, threads, and runtime subscriptions.

Real brokers with real peer inboxes share one in-process bus: a question published to a
topic reaches whoever follows it, answers are events that come back to the asker and to
everyone following the thread, and what a session follows survives a restart.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.forge_mcp.models import ForgeApiError
from niuu.mesh.identity import MeshIdentity
from niuu.mesh.participant import MeshParticipant
from skuld.config import MeshConfig
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.mesh_adapter import SkuldMeshAdapter
from skuld.mesh_follows import MESH_SUBSCRIPTION_FRAME
from skuld.mesh_topics import (
    InvalidTopicError,
    MeshTopics,
    inbox_topic,
    thread_topic,
    validate_subscribable_topic,
)
from skuld.peer_inbox import render_peer_turn
from sleipnir.adapters.in_process import InProcessBus
from tests.test_skuld.test_peer_inbox import _inbox_broker, _sent_texts

QUESTION = "how do I rotate the signing key?"


class Node:
    """One session: a real broker (peer inbox, durable log buffer) on a real mesh adapter."""

    def __init__(self, tmp_path, bus, name, *, consumes=(), produces=(), session_id="") -> None:
        directory = tmp_path / f"{name}-{len(list(tmp_path.iterdir()))}"
        directory.mkdir(exist_ok=True)
        self.name = name
        self.consumes = tuple(consumes)
        self.broker = _inbox_broker(directory)
        self.broker.session_id = session_id or f"session-{name}"
        self.broker._settings.mesh = MeshConfig(enabled=True, peer_id=name, rpc_timeout_s=5.0)
        self.broker._mesh_topics = MeshTopics(self.consumes, tuple(produces), persona_id=name)
        self.broker._mesh_identity = MeshIdentity(
            peer_id=name,
            realm_id="r",
            persona=name,
            capabilities=[],
            permission_mode="read_only",
            version="1",
            consumes_event_types=list(self.consumes),
        )
        mesh = SleipnirMeshAdapter(
            publisher=bus, subscriber=bus, own_peer_id=name, rpc_timeout_s=5.0
        )
        self.adapter = SkuldMeshAdapter(
            participant=MeshParticipant(mesh=mesh, discovery=None, peer_id=name),
            config=MeshConfig(enabled=True, peer_id=name, rpc_timeout_s=5.0),
            session_id=self.broker.session_id,
            peer_inbox=self.broker,
            consumes_event_types=self.consumes,
        )
        self.broker._mesh_adapter = self.adapter
        self.host = BrokerForgeMcpHost(self.broker, client=SimpleNamespace())

    async def start(self) -> None:
        await self.adapter.start()
        await self.broker.apply_restored_follows()

    async def stop(self) -> None:
        await self.adapter.stop()

    @property
    def inbox(self):
        """The messages this session holds, in the order they arrived."""
        return list(self.broker._peer_inbox.values())

    def held(self) -> list[tuple[str, str, str]]:
        """(sender, content, in_reply_to) of each held message."""
        return [
            (e.envelope.sender.id, e.envelope.content, e.envelope.in_reply_to) for e in self.inbox
        ]

    async def publish(self, topic, content, *, request_id, correlation_id="", in_reply_to=""):
        return await self.host.publish_mesh_event(
            topic,
            content,
            request_id=request_id,
            correlation_id=correlation_id,
            in_reply_to=in_reply_to,
        )


@pytest.fixture
async def flock(tmp_path):
    bus = InProcessBus()
    nodes: list[Node] = []

    async def add(name, **kwargs) -> Node:
        node = Node(tmp_path, bus, name, **kwargs)
        await node.start()
        nodes.append(node)
        return node

    async def settle() -> None:
        """Let published events reach their handlers and the inboxes finish what they spawned."""
        for _ in range(4):
            await bus.flush()
            await asyncio.sleep(0.01)
            tasks = [t for node in nodes for t in list(node.broker._peer_inbox_tasks)]
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)

    flock = SimpleNamespace(add=add, settle=settle, bus=bus, tmp_path=tmp_path)
    yield flock
    for node in nodes:
        await node.stop()


class TestAQuestionWithManyAnswers:
    async def _asked(self, flock):
        asker = await flock.add("asker", produces=["help.needed"])
        first = await flock.add("first", consumes=["help.needed"], produces=["answer.given"])
        second = await flock.add("second", consumes=["help.needed"], produces=["answer.given"])
        observer = await flock.add("observer")
        question = await asker.publish("help.needed", QUESTION, request_id="q1")
        await flock.settle()
        return asker, first, second, observer, question

    async def test_everyone_following_the_topic_gets_the_question(self, flock) -> None:
        asker, first, second, observer, question = await self._asked(flock)

        for node in (first, second):
            [entry] = node.inbox
            assert entry.envelope.message_id == question["event_id"]
            assert entry.envelope.thread == question["event_id"]
            assert QUESTION in entry.envelope.content
        assert observer.inbox == [] and asker.inbox == []

    async def test_two_subscribers_both_answer_by_event_and_the_asker_gets_both_grouped(
        self, flock
    ) -> None:
        asker, first, second, observer, question = await self._asked(flock)
        thread = question["event_id"]

        await first.publish(
            "answer.given", "rotate it in the vault", request_id="a1", in_reply_to=thread
        )
        await second.publish(
            "answer.given", "use the rotate script", request_id="a2", in_reply_to=thread
        )
        await flock.settle()

        # The asker subscribed to nothing, and holds both answers, linked to its question.
        assert sorted(asker.held()) == [
            ("first", "[Request for event type answer.given]\nrotate it in the vault", thread),
            ("second", "[Request for event type answer.given]\nuse the rotate script", thread),
        ]
        assert {e.envelope.thread for e in asker.inbox} == {thread}
        # They are rendered together under their thread.
        text = render_peer_turn(asker.inbox)
        assert text.count(f"[Thread {thread}: 2 messages]") == 1
        assert "rotate it in the vault" in text and "use the rotate script" in text
        assert text.index("rotate it in the vault") < text.index("use the rotate script")

    async def test_answers_reach_the_harness_together_at_the_turn_boundary(self, flock) -> None:
        asker, first, second, _, question = await self._asked(flock)
        thread = question["event_id"]
        asker.broker._activity_state = "active"  # mid-turn: nothing is handed over yet

        await first.publish("answer.given", "answer one", request_id="a1", in_reply_to=thread)
        await second.publish("answer.given", "answer two", request_id="a2", in_reply_to=thread)
        await flock.settle()
        assert _sent_texts(asker.broker) == []

        asker.broker._activity_state = "idle"
        await asker.broker._inject_at_turn_boundary()
        await flock.settle()

        [turn] = _sent_texts(asker.broker)
        assert f"[Thread {thread}: 2 messages]" in turn
        assert "answer one" in turn and "answer two" in turn

    async def test_a_third_agent_following_the_thread_sees_the_answers(self, flock) -> None:
        asker, first, second, observer, question = await self._asked(flock)
        thread = question["event_id"]
        followed = await observer.host.mesh_subscribe(topic="", correlation_id=thread)
        assert followed["status"] == "followed" and followed["correlation_id"] == thread

        await first.publish("answer.given", "answer one", request_id="a1", in_reply_to=thread)
        await second.publish("answer.given", "answer two", request_id="a2", in_reply_to=thread)
        await flock.settle()

        assert sorted(sender for sender, _, _ in observer.held()) == ["first", "second"]
        assert {e.envelope.thread for e in observer.inbox} == {thread}
        # The responders did not follow the thread, so they do not see each other's answers.
        assert [e.envelope.message_id for e in first.inbox] == [thread]

    async def test_an_agent_that_answers_with_its_own_later_event_starts_a_new_thread(
        self, flock
    ) -> None:
        asker, first, _, _, question = await self._asked(flock)

        # "make changes": unprompted, as its own event, later.
        later = await first.publish("answer.given", "also, please change X", request_id="m1")
        await flock.settle()

        assert later["correlation_id"] == later["event_id"] != question["event_id"]
        assert asker.inbox == []  # nobody follows it; it is not a reply to anything

    async def test_a_named_correlation_id_threads_the_root_and_the_answers(self, flock) -> None:
        asker = await flock.add("asker", produces=["help.needed"])
        first = await flock.add("first", consumes=["help.needed"], produces=["answer.given"])
        follower = await flock.add("follower")
        await follower.host.mesh_subscribe(topic="", correlation_id="pr-12")

        question = await asker.publish(
            "help.needed", QUESTION, request_id="q", correlation_id="pr-12"
        )
        await flock.settle()
        await first.publish(
            "answer.given",
            "yes",
            request_id="a",
            in_reply_to=question["event_id"],
            correlation_id="pr-12",
        )
        await flock.settle()

        assert question["correlation_id"] == "pr-12"
        [root] = [e for e in first.inbox]
        assert root.envelope.thread == "pr-12"
        assert [e.envelope.thread for e in follower.inbox] == ["pr-12", "pr-12"]
        assert len(asker.inbox) == 1  # the answer, by its inbox topic


class TestAnUnansweredQuestion:
    async def test_it_stays_silent_with_no_error_and_no_wait(self, flock) -> None:
        asker = await flock.add("asker", produces=["help.needed"])
        listener = await flock.add("listener", consumes=["help.needed"])

        result = await asyncio.wait_for(
            asker.publish("help.needed", QUESTION, request_id="q"), timeout=2
        )
        await flock.settle()

        assert result["status"] == "published"
        assert len(listener.inbox) == 1  # heard, and did not answer
        assert asker.inbox == [] and asker.broker.pending_peer_message_ids() == []
        assert _sent_texts(asker.broker) == []

    async def test_a_question_nobody_follows_is_just_as_quiet(self, flock) -> None:
        asker = await flock.add("asker", produces=["help.needed"])

        result = await asker.publish("help.needed", QUESTION, request_id="q")
        await flock.settle()

        assert result["status"] == "published" and asker.inbox == []


class TestFollowingAtRuntime:
    async def test_a_topic_followed_at_runtime_delivers_and_is_announced(self, flock) -> None:
        reader = await flock.add("reader")
        writer = await flock.add("writer", produces=["status.update"])

        followed = await reader.host.mesh_subscribe(topic="status.update", correlation_id="")
        await writer.publish("status.update", "deploy done", request_id="s1")
        await flock.settle()

        assert followed["status"] == "followed"
        assert followed["followed"] == [{"topic": "status.update"}]
        assert [content for _, content, _ in reader.held()] == [
            "[Request for event type status.update]\ndeploy done"
        ]
        assert reader.broker._mesh_identity.consumes_event_types == ["status.update"]

    async def test_unsubscribing_stops_it_and_the_announcement_forgets_it(self, flock) -> None:
        reader = await flock.add("reader")
        writer = await flock.add("writer", produces=["status.update"])
        await reader.host.mesh_subscribe(topic="status.update", correlation_id="")

        gone = await reader.host.mesh_unsubscribe(topic="status.update", correlation_id="")
        await writer.publish("status.update", "deploy done", request_id="s1")
        await flock.settle()

        assert gone["status"] == "unfollowed" and gone["followed"] == []
        assert reader.inbox == []
        assert reader.broker._mesh_identity.consumes_event_types == []

    async def test_following_twice_is_not_an_error_and_a_standing_topic_is_already_followed(
        self, flock
    ) -> None:
        reader = await flock.add("reader", consumes=["code.requested"])
        await reader.host.mesh_subscribe(topic="status.update", correlation_id="")

        again = await reader.host.mesh_subscribe(topic="status.update", correlation_id="")
        standing = await reader.host.mesh_subscribe(topic="code.requested", correlation_id="")

        assert again["status"] == standing["status"] == "already"
        assert standing["standing"] == ["code.requested"]
        assert [m["topic"] for m in standing["followed"]] == ["status.update"]

    async def test_a_standing_topic_cannot_be_unsubscribed_and_an_unknown_one_is_an_error(
        self, flock
    ) -> None:
        reader = await flock.add("reader", consumes=["code.requested"])

        with pytest.raises(ForgeApiError, match="standing subscription"):
            await reader.host.mesh_unsubscribe(topic="code.requested", correlation_id="")
        with pytest.raises(ForgeApiError, match="does not follow 'nothing.here'"):
            await reader.host.mesh_unsubscribe(topic="nothing.here", correlation_id="")

    @pytest.mark.parametrize(
        ("topic", "text"),
        [
            ("Not A Topic", "is not a topic"),
            ("", "give exactly one"),
            ("thread.tabc", "platform topic"),
            ("inbox.pabc", "platform topic"),
        ],
    )
    async def test_a_topic_the_mesh_cannot_carry_or_reserves_is_refused(
        self, flock, topic, text
    ) -> None:
        reader = await flock.add("reader")

        with pytest.raises(ForgeApiError, match=text) as excinfo:
            await reader.host.mesh_subscribe(topic=topic, correlation_id="")

        assert excinfo.value.status == 409
        assert reader.broker._mesh_follows == {}

    async def test_a_session_without_a_durable_log_follows_nothing(self, flock) -> None:
        reader = await flock.add("reader")
        reader.broker.volundr_api_url = ""

        with pytest.raises(ForgeApiError, match="no durable Forge log"):
            await reader.host.mesh_subscribe(topic="status.update", correlation_id="")

        assert reader.adapter.followed_topics == ()

    async def test_a_follow_the_log_did_not_store_is_not_started(self, flock) -> None:
        reader = await flock.add("reader")
        reader.broker._await_event_log_receipt.return_value = False

        with pytest.raises(ForgeApiError, match="was not stored"):
            await reader.host.mesh_subscribe(topic="status.update", correlation_id="")

        assert reader.adapter.followed_topics == () and reader.broker._mesh_follows == {}


class TestAcrossARestart:
    def _frames(self, node: Node) -> list:
        """The durable log as the restarted broker reads it back."""
        return [
            SimpleNamespace(kind=entry["kind"], payload=entry["payload"])
            for entry in node.broker._event_log_buffer
        ]

    async def _restart(self, flock, node: Node, **kwargs) -> Node:
        frames = self._frames(node)
        await node.stop()
        reborn = Node(
            flock.tmp_path, flock.bus, node.name, session_id=node.broker.session_id, **kwargs
        )
        reborn.broker._restore_durable_mesh_follows(frames)
        await reborn.start()
        return reborn

    async def test_what_a_session_followed_is_followed_again_after_a_restart(self, flock) -> None:
        reader = await flock.add("reader")
        writer = await flock.add("writer", produces=["status.update", "news.item"])
        await reader.host.mesh_subscribe(topic="status.update", correlation_id="")
        await reader.host.mesh_subscribe(topic="", correlation_id="thread-1")
        await reader.host.mesh_subscribe(topic="news.item", correlation_id="")
        await reader.host.mesh_unsubscribe(topic="news.item", correlation_id="")
        frames = self._frames(reader)
        assert [f.kind for f in frames].count(MESH_SUBSCRIPTION_FRAME) == 4

        reborn = await self._restart(flock, reader, consumes=("code.requested",))
        await writer.publish("status.update", "after the restart", request_id="s1")
        await writer.publish("news.item", "unfollowed before the restart", request_id="n1")
        await writer.publish(
            "status.update", "in a thread", request_id="t1", correlation_id="thread-1"
        )
        await flock.settle()

        assert set(reborn.adapter.followed_topics) == {
            "status.update",
            thread_topic("thread-1"),
        }
        contents = [content for _, content, _ in reborn.held()]
        assert contents.count("[Request for event type status.update]\nafter the restart") == 1
        assert not any("unfollowed" in content for content in contents)
        assert any("in a thread" in content for content in contents)
        # And it is announced again, with the standing topic first.
        assert reborn.broker._mesh_identity.consumes_event_types == [
            "code.requested",
            "status.update",
        ]
        await reborn.stop()

    async def test_a_restored_follow_the_mesh_cannot_subscribe_fails_the_start(self, flock) -> None:
        reader = await flock.add("reader")
        await reader.host.mesh_subscribe(topic="status.update", correlation_id="")
        frames = self._frames(reader)
        await reader.stop()
        reborn = Node(flock.tmp_path, flock.bus, "reader", session_id=reader.broker.session_id)
        reborn.broker._restore_durable_mesh_follows(frames)
        reborn.adapter.follow = _boom  # type: ignore[method-assign]
        await reborn.adapter.start()

        with pytest.raises(RuntimeError, match="no such transport"):
            await reborn.broker.apply_restored_follows()


async def _boom(topic):
    raise RuntimeError("no such transport")


class TestTopicNames:
    def test_a_thread_and_an_inbox_have_their_own_topics_that_never_collide(self) -> None:
        assert thread_topic("PR-12") != thread_topic("pr_12")
        assert thread_topic("x").startswith("thread.t") and inbox_topic("x").startswith("inbox.p")
        assert thread_topic("x") != inbox_topic("x")

    @pytest.mark.parametrize("topic", ["review.requested", "help.needed", "a", "a-b.c_d"])
    def test_ordinary_topics_are_accepted(self, topic) -> None:
        assert validate_subscribable_topic(f" {topic} ") == topic

    @pytest.mark.parametrize("topic", ["", "1abc", "a..b", "a b", "thread.x", "inbox.p1"])
    def test_other_names_are_refused(self, topic) -> None:
        with pytest.raises(InvalidTopicError):
            validate_subscribable_topic(topic)


class TestRendering:
    def _entry(self, message_id, thread="", content="hello"):
        from datetime import UTC, datetime

        from niuu.domain.peer_message import PeerAddress, PeerKind, PeerMessage
        from skuld.peer_inbox import PeerInboxEntry

        return PeerInboxEntry(
            envelope=PeerMessage(
                message_id=message_id,
                sender=PeerAddress(PeerKind.MESH_PEER, f"from-{message_id}"),
                recipient=PeerAddress(PeerKind.SESSION, "me"),
                sent_at=datetime(2026, 11, 8, tzinfo=UTC),
                content=content,
                thread=thread,
            ),
            accepted_at="2026-11-08T00:00:00+00:00",
        )

    def test_messages_of_a_thread_are_grouped_and_others_keep_their_place(self) -> None:
        text = render_peer_turn(
            [
                self._entry("a1", "t1", "first answer"),
                self._entry("solo", "", "unrelated"),
                self._entry("b1", "t2", "other thread"),
                self._entry("a2", "t1", "second answer"),
            ]
        )

        assert "4 messages arrived while you were working, grouped by thread" in text
        assert text.count("[Thread t1: 2 messages]") == 1
        assert "[Thread t2:" not in text  # a thread of one is just a message
        order = [
            text.index(w) for w in ("first answer", "second answer", "unrelated", "other thread")
        ]
        assert order == sorted(order)

    def test_without_threads_the_turn_reads_as_before(self) -> None:
        text = render_peer_turn([self._entry("x"), self._entry("y")])

        assert text.startswith("[2 messages arrived while you were working, oldest first]")
        assert "[Thread" not in text

    def test_a_message_names_its_thread_unless_it_is_the_thread_root(self) -> None:
        from niuu.domain.peer_message import render_for_harness

        assert "[Thread t1]" in render_for_harness(self._entry("m1", "t1").envelope)
        assert "[Thread" not in render_for_harness(self._entry("m1", "m1").envelope)

    def test_a_thread_survives_the_wire_and_a_message_from_before_threads_still_parses(
        self,
    ) -> None:
        from niuu.domain.peer_message import PeerMessage

        wire = self._entry("m1", "t1").envelope.to_wire()
        assert PeerMessage.from_wire(wire).thread == "t1"
        del wire["thread"]
        assert PeerMessage.from_wire(wire).thread == ""
