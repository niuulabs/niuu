"""Questions asked by publishing: many answers or none, threads, and runtime subscriptions.

Real brokers with real peer inboxes share one in-process bus: a question published to a
topic reaches whoever follows it, answers are events that come back to the asker and to
everyone following the thread, and what a session follows survives a restart.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.domain.peer_message import PeerAddress, PeerKind, PeerMessage
from niuu.forge_mcp.models import ForgeApiError
from niuu.mesh.attestation import VERIFIED_SENDER_KEY
from niuu.mesh.identity import MeshIdentity, MeshPeer
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


class CertifiedMesh(SleipnirMeshAdapter):
    """A mesh that tells its handlers who sent a message, as the attested mesh does after it
    has verified the certificate: ``certs`` maps every id a sender may name itself by (its mesh
    peer id or its session id) to its one certified ``(peer_id, session_id)``."""

    def __init__(self, *args, certs, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._certs = certs

    def _summary(self, claimed):
        cert = self._certs.get(claimed)
        return None if cert is None else {"peer_id": cert[0], "session_id": cert[1]}

    def set_rpc_handler(self, handler) -> None:
        async def stamped(body):
            claimed = ((body.get("envelope") or {}).get("sender") or {}).get("id")
            summary = self._summary(claimed)
            return await handler({**body, VERIFIED_SENDER_KEY: summary} if summary else body)

        super().set_rpc_handler(stamped)

    async def subscribe(self, topic, handler) -> None:
        async def stamped(event):
            summary = self._summary(event.source)
            await handler(
                replace(event, attestation={VERIFIED_SENDER_KEY: summary}) if summary else event
            )

        await super().subscribe(topic, stamped)


class Node:
    """One session: a real broker (peer inbox, durable log buffer) on a real mesh adapter."""

    def __init__(
        self,
        tmp_path,
        bus,
        name,
        *,
        consumes=(),
        produces=(),
        session_id="",
        description="",
        certs=None,
        signed=True,
    ) -> None:
        directory = tmp_path / f"{name}-{len(list(tmp_path.iterdir()))}"
        directory.mkdir(exist_ok=True)
        self.name = name
        self.consumes = tuple(consumes)
        self.produces = tuple(produces)
        self.description = description
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
        mesh_kwargs = {
            "publisher": bus,
            "subscriber": bus,
            "own_peer_id": name,
            "rpc_timeout_s": 5.0,
        }
        mesh = (
            SleipnirMeshAdapter(**mesh_kwargs)
            if certs is None
            else CertifiedMesh(**mesh_kwargs, certs=certs)
        )
        self.adapter = SkuldMeshAdapter(
            participant=MeshParticipant(mesh=mesh, discovery=None, peer_id=name),
            config=MeshConfig(
                enabled=True,
                peer_id=name,
                rpc_timeout_s=5.0,
                attestation={"required": signed},
            ),
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

    async def ask(self, content, *, request_id="ask-1", producers_of="", peers=(), wait_s=0):
        return await self.host.mesh_ask(
            content,
            producers_of=producers_of,
            peers=list(peers),
            request_id=request_id,
            wait_s=wait_s,
        )

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

    def peers_seen_by(me: Node) -> dict[str, MeshPeer]:
        """What discovery would tell ``me``: every other node, as it announces itself."""
        return {
            node.name: MeshPeer(
                peer_id=node.name,
                realm_id="r",
                persona=node.name,
                capabilities=[],
                permission_mode="read_only",
                version="1",
                display_name=node.name,
                participant_type="skuld",
                description=node.description,
                consumes_event_types=list(node.consumes),
                emits_event_types=list(node.produces),
            )
            for node in nodes
            if node is not me
        }

    async def add(name, **kwargs) -> Node:
        node = Node(tmp_path, bus, name, **kwargs)
        node.broker._mesh_discovery = SimpleNamespace(peers=lambda me=node: peers_seen_by(me))
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

    flock = SimpleNamespace(add=add, settle=settle, bus=bus, tmp_path=tmp_path, nodes=nodes)
    yield flock
    for node in nodes:
        await node.stop()


class TestAQuestionWithManyAnswers:
    async def _asked(self, flock):
        asker = await flock.add("asker", produces=["advice.wanted"])
        first = await flock.add("first", consumes=["advice.wanted"], produces=["answer.given"])
        second = await flock.add("second", consumes=["advice.wanted"], produces=["answer.given"])
        observer = await flock.add("observer")
        question = await asker.publish("advice.wanted", QUESTION, request_id="q1")
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
        asker = await flock.add("asker", produces=["advice.wanted"])
        first = await flock.add("first", consumes=["advice.wanted"], produces=["answer.given"])
        follower = await flock.add("follower")
        await follower.host.mesh_subscribe(topic="", correlation_id="pr-12")

        question = await asker.publish(
            "advice.wanted", QUESTION, request_id="q", correlation_id="pr-12"
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
        asker = await flock.add("asker", produces=["advice.wanted"])
        listener = await flock.add("listener", consumes=["advice.wanted"])

        result = await asyncio.wait_for(
            asker.publish("advice.wanted", QUESTION, request_id="q"), timeout=2
        )
        await flock.settle()

        assert result["status"] == "published"
        assert len(listener.inbox) == 1  # heard, and did not answer
        assert asker.inbox == [] and asker.broker.pending_peer_message_ids() == []
        assert _sent_texts(asker.broker) == []

    async def test_a_question_nobody_follows_is_just_as_quiet(self, flock) -> None:
        asker = await flock.add("asker", produces=["advice.wanted"])

        result = await asker.publish("advice.wanted", QUESTION, request_id="q")
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

    @pytest.mark.parametrize("topic", ["review.requested", "advice.wanted", "a", "a-b.c_d"])
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


class TestAQuestionToTheProducersOfATopic:
    async def _flock(self, flock):
        asker = await flock.add("asker")
        # Two sources of deployment knowledge, one consumer of it, and a bystander.
        source_a = await flock.add(
            "source-a",
            produces=["deploy.status", "answer.given"],
            description="Runs the production deploys.",
        )
        source_b = await flock.add(
            "source-b",
            produces=["deploy.status", "answer.given"],
            description="Runs the staging deploys.",
        )
        consumer = await flock.add("consumer", consumes=["deploy.status"])
        other = await flock.add("other", produces=["billing.report"])
        return asker, source_a, source_b, consumer, other

    async def test_the_question_reaches_the_producers_and_not_the_consumers(self, flock) -> None:
        asker, source_a, source_b, consumer, other = await self._flock(flock)

        sent = await asker.ask("did the deploy finish?", producers_of="deploy.status")
        await flock.settle()

        assert [peer["peer"] for peer in sent["delivered"]] == ["source-a", "source-b"]
        assert sent["failed"] == [] and sent["asked"] == "the peers that produce 'deploy.status'"
        for node in (source_a, source_b):
            [entry] = node.inbox
            assert entry.envelope.message_id == sent["question_id"]
            assert entry.envelope.thread == sent["question_id"]
            assert entry.envelope.reply.target == "asker"
            assert "because you announce that you produce 'deploy.status'" in entry.envelope.content
            assert "did the deploy finish?" in entry.envelope.content
        assert consumer.inbox == [] and other.inbox == [] and asker.inbox == []

    async def test_the_askers_own_produces_does_not_limit_a_question(self, flock) -> None:
        asker, *_ = await self._flock(flock)
        assert asker.produces == ()  # it could not publish deploy.status as an event

        sent = await asker.ask("did the deploy finish?", producers_of="deploy.status")

        assert len(sent["delivered"]) == 2

    async def test_two_answers_come_back_correlated_one_by_event_one_by_reply(self, flock) -> None:
        asker, source_a, source_b, *_ = await self._flock(flock)
        sent = await asker.ask("did the deploy finish?", producers_of="deploy.status")
        question = sent["question_id"]
        await flock.settle()
        follower = await flock.add("follower")
        await follower.host.mesh_subscribe(topic="", correlation_id=question)

        # One recipient answers by event (everyone following the thread sees it).
        await source_a.publish(
            "answer.given", "production: done", request_id="a", in_reply_to=question
        )
        # The other answers directly, as the reply tool does.
        await source_b.host.send_mesh_message(
            "asker",
            "staging: still running",
            expect_reply=False,
            request_id="b",
            in_reply_to=question,
            thread=question,
        )
        await flock.settle()

        assert sorted(sender for sender, _, _ in asker.held()) == [
            "session-source-b",  # the direct reply comes from the session
            "source-a",  # the event comes from the mesh peer
        ]
        assert {e.envelope.in_reply_to for e in asker.inbox} == {question}
        assert {e.envelope.thread for e in asker.inbox} == {question}
        text = render_peer_turn(asker.inbox)
        assert text.count(f"[Thread {question}: 2 messages]") == 1
        assert "production: done" in text and "staging: still running" in text
        # Only the answer published as an event reached the follower.
        assert [e.envelope.content for e in follower.inbox] == [
            "[Request for event type answer.given]\nproduction: done"
        ]

    async def test_a_question_nobody_answers_is_quiet_after_it_was_delivered(self, flock) -> None:
        asker, source_a, *_ = await self._flock(flock)

        sent = await asker.ask("did the deploy finish?", producers_of="deploy.status")
        await flock.settle()

        assert len(sent["delivered"]) == 2 and len(source_a.inbox) == 1
        assert asker.inbox == []

    async def test_a_topic_no_peer_produces_is_refused_not_published_into_silence(
        self, flock
    ) -> None:
        asker, source_a, source_b, consumer, other = await self._flock(flock)

        with pytest.raises(ForgeApiError, match="no peer announces that it produces") as excinfo:
            await asker.ask("anyone?", producers_of="deploy.statuz")
        await flock.settle()

        assert excinfo.value.status == 409
        assert "Nothing was sent" in excinfo.value.detail and "mesh_peers" in excinfo.value.detail
        for node in (source_a, source_b, consumer, other):
            assert node.inbox == []

    async def test_a_peer_that_did_not_announce_what_it_produces_is_counted_not_matched(
        self, flock
    ) -> None:
        asker, source_a, *_ = await self._flock(flock)
        silent = flock.nodes[-1]
        original = asker.broker._mesh_discovery.peers

        def peers():
            table = original()
            table[silent.name].not_announced = ["emits_event_types"]
            table[silent.name].emits_event_types = []
            return table

        asker.broker._mesh_discovery = SimpleNamespace(peers=peers)

        with pytest.raises(ForgeApiError, match=r"1 of them did not announce what they produce"):
            await asker.ask("anyone?", producers_of="billing.report")

    async def test_explicit_recipients_are_chosen_from_mesh_peers_by_description(
        self, flock
    ) -> None:
        asker, source_a, source_b, consumer, other = await self._flock(flock)

        table = await asker.host.mesh_peers(role="", capability="", consumes="", produces="")
        chosen = [p["peer_id"] for p in table["peers"] if "staging" in p["description"]]
        sent = await asker.ask("is staging healthy?", peers=chosen)
        await flock.settle()

        assert chosen == ["source-b"] and sent["asked"] == "the peers chosen"
        assert [len(n.inbox) for n in (source_a, source_b, consumer, other)] == [0, 1, 0, 0]
        assert "because you were chosen to answer" in source_b.inbox[0].envelope.content

    async def test_a_peer_that_is_not_in_the_table_is_refused_by_name(self, flock) -> None:
        asker, source_a, *_ = await self._flock(flock)

        with pytest.raises(
            ForgeApiError, match="not in this session's peer table: ghost"
        ) as excinfo:
            await asker.ask("hello?", peers=["source-a", "ghost"])
        await flock.settle()

        assert excinfo.value.status == 404 and source_a.inbox == []

    async def test_a_recipient_that_cannot_be_reached_is_listed_and_the_rest_still_get_it(
        self, flock
    ) -> None:
        asker, source_a, source_b, *_ = await self._flock(flock)
        real = asker.adapter.send_peer_message

        async def flaky(peer, message):
            if peer == "source-a":
                raise TimeoutError
            return await real(peer, message)

        asker.adapter.send_peer_message = flaky  # type: ignore[method-assign]

        sent = await asker.ask("did the deploy finish?", producers_of="deploy.status")
        await flock.settle()

        assert [p["peer"] for p in sent["delivered"]] == ["source-b"]
        assert sent["failed"][0]["peer"] == "source-a"
        assert "mesh.rpc_timeout_s" in sent["failed"][0]["error"]
        assert source_a.inbox == [] and len(source_b.inbox) == 1

    async def test_when_it_reaches_nobody_the_ask_is_an_error_naming_each_failure(
        self, flock
    ) -> None:
        asker, *_ = await self._flock(flock)

        async def down(peer, message):
            raise ConnectionError("nats down")

        asker.adapter.send_peer_message = down  # type: ignore[method-assign]

        with pytest.raises(ForgeApiError, match="reached none of") as excinfo:
            await asker.ask("did the deploy finish?", producers_of="deploy.status")

        assert excinfo.value.status == 502 and "nats down" in excinfo.value.detail

    async def test_a_retry_with_the_same_request_id_asks_once(self, flock) -> None:
        asker, source_a, *_ = await self._flock(flock)

        first = await asker.ask("did it finish?", producers_of="deploy.status", request_id="r")
        second = await asker.ask("did it finish?", producers_of="deploy.status", request_id="r")
        await flock.settle()

        assert first["question_id"] == second["question_id"]
        assert len(source_a.inbox) == 1  # the repeat is the same message


class TestWaitingForAnswers:
    async def _asked(self, flock):
        asker = await flock.add("asker")
        first = await flock.add("first", produces=["deploy.status"])
        second = await flock.add("second", produces=["deploy.status"])
        return asker, first, second

    async def test_the_wait_returns_every_answer_so_far_and_consumes_them(self, flock) -> None:
        asker, first, second = await self._asked(flock)

        async def answer_later() -> None:
            await asyncio.sleep(0.05)
            for node in (first, second):
                question = node.inbox[0].envelope
                await node.host.send_mesh_message(
                    "asker",
                    f"{node.name} says yes",
                    expect_reply=False,
                    request_id=node.name,
                    in_reply_to=question.message_id,
                    thread=question.thread,
                )

        async def ask():
            return await asker.ask("done?", producers_of="deploy.status", wait_s=0.4)

        result, _ = await asyncio.gather(ask(), answer_later())

        assert result["waited_s"] == 0.4 and result["answered"] == 2
        assert sorted(a["content"] for a in result["answers"]) == [
            "first says yes",
            "second says yes",
        ]
        # They were taken by the call, so the harness is not handed them again.
        await flock.settle()
        assert _sent_texts(asker.broker) == []

    async def test_the_wait_does_not_stop_at_the_first_answer(self, flock) -> None:
        asker, first, second = await self._asked(flock)

        async def answer_early_and_late() -> None:
            await asyncio.sleep(0.02)
            question = first.inbox[0].envelope
            await first.host.send_mesh_message(
                "asker", "early", expect_reply=False, request_id="e",
                in_reply_to=question.message_id, thread=question.thread,
            )  # fmt: skip
            await asyncio.sleep(0.15)
            await second.host.send_mesh_message(
                "asker", "late", expect_reply=False, request_id="l",
                in_reply_to=question.message_id, thread=question.thread,
            )  # fmt: skip

        async def ask():
            return await asker.ask("done?", producers_of="deploy.status", wait_s=0.4)

        result, _ = await asyncio.gather(ask(), answer_early_and_late())

        assert [a["content"] for a in result["answers"]] == ["early", "late"]

    async def test_without_a_wait_nothing_waits_and_answers_arrive_as_messages(self, flock) -> None:
        asker, first, _ = await self._asked(flock)

        result = await asyncio.wait_for(asker.ask("done?", producers_of="deploy.status"), timeout=2)

        assert "answers" not in result and "waited_s" not in result

    async def test_an_answer_after_the_wait_is_delivered_normally(self, flock) -> None:
        asker, first, _ = await self._asked(flock)
        result = await asker.ask("done?", producers_of="deploy.status", wait_s=0.05)
        question = first.inbox[0].envelope

        await first.host.send_mesh_message(
            "asker", "too late for the wait", expect_reply=False, request_id="x",
            in_reply_to=question.message_id, thread=question.thread,
        )  # fmt: skip
        await flock.settle()

        assert result["answers"] == []
        assert [content for _, content, _ in asker.held()] == ["too late for the wait"]


def _certs(*names: str) -> dict[str, tuple[str, str]]:
    """Each name's certified (peer id, session id), under both ids it may name itself by."""
    return {key: (name, f"session-{name}") for name in names for key in (name, f"session-{name}")}


ALL_CERTS = _certs("asker", "first", "second", "outsider")


class TestStoppingTheWaitEarly:
    """The wait ends when every peer the question reached has answered, else at the deadline."""

    async def _asked(self, flock):
        # Signed: every answer carries its verified sender, however it was sent.
        asker = await flock.add("asker", certs=ALL_CERTS)
        first = await flock.add("first", produces=["deploy.status"], certs=ALL_CERTS)
        second = await flock.add("second", produces=["deploy.status"], certs=ALL_CERTS)
        return asker, first, second

    async def _reply(self, node, content, *, delay=0.0):
        await asyncio.sleep(delay)
        question = node.inbox[0].envelope
        await node.host.send_mesh_message(
            "asker",
            content,
            expect_reply=False,
            request_id=f"{node.name}-{content}",
            in_reply_to=question.message_id,
            thread=question.thread,
        )

    async def _ask_while(self, asker, *answerers, wait_s=3.0):
        started = time.monotonic()
        result, *_ = await asyncio.gather(
            asker.ask("done?", producers_of="deploy.status", wait_s=wait_s), *answerers
        )
        return result, time.monotonic() - started

    async def test_it_returns_as_soon_as_everyone_asked_has_answered(self, flock) -> None:
        asker, first, second = await self._asked(flock)

        async def ask():
            return await asker.ask("done?", producers_of="deploy.status", wait_s=5)

        started = time.monotonic()

        async def answers():
            await self._reply(first, "one", delay=0.05)
            await self._reply(second, "two", delay=0.05)

        # The answerers wait for the question to land, so they start after a beat.
        async def later():
            await asyncio.sleep(0.05)
            await answers()

        result, _ = await asyncio.gather(ask(), later())

        assert result["ended"] == "all_asked_answered" and time.monotonic() - started < 2
        assert result["answered"] == 2 and result["not_yet_answered"] == []

    async def test_it_returns_at_the_deadline_with_what_it_has_and_names_who_is_missing(
        self, flock
    ) -> None:
        asker, first, second = await self._asked(flock)

        async def later():
            await asyncio.sleep(0.05)
            await self._reply(first, "only me")

        result, elapsed = await self._ask_while(asker, later(), wait_s=0.4)

        assert result["ended"] == "deadline" and elapsed >= 0.35
        assert [a["content"] for a in result["answers"]] == ["only me"]
        assert result["not_yet_answered"] == ["second"]

    async def test_an_answer_from_a_peer_nobody_asked_is_returned_and_ends_nothing(
        self, flock
    ) -> None:
        asker, first, second = await self._asked(flock)
        outsider = await flock.add("outsider", certs=ALL_CERTS)

        async def later():
            await asyncio.sleep(0.05)
            question = first.inbox[0].envelope
            await outsider.host.send_mesh_message(
                "asker",
                "I know this too",
                expect_reply=False,
                request_id="o",
                in_reply_to=question.message_id,
                thread=question.thread,
            )
            await self._reply(first, "one")

        result, _ = await self._ask_while(asker, later(), wait_s=0.4)

        assert result["ended"] == "deadline"  # the outsider's answer did not stand in for second
        contents = sorted(a["content"] for a in result["answers"])
        assert contents == ["I know this too", "one"]
        assert result["not_yet_answered"] == ["second"]

    async def test_a_recipient_the_question_never_reached_is_not_waited_for(self, flock) -> None:
        asker, first, second = await self._asked(flock)
        real = asker.adapter.send_peer_message

        async def flaky(peer, message):
            if peer == "second":
                raise TimeoutError
            return await real(peer, message)

        asker.adapter.send_peer_message = flaky  # type: ignore[method-assign]

        async def later():
            await asyncio.sleep(0.05)
            await self._reply(first, "one")

        result, elapsed = await self._ask_while(asker, later(), wait_s=3)

        assert result["ended"] == "all_asked_answered" and elapsed < 2
        assert [f["peer"] for f in result["failed"]] == ["second"]


class TestOneIdentityPerAnswerer:
    """With mesh signing, every answer carries a verified sender: one peer is one answerer."""

    CERTS = {
        "first": ("first", "session-first"),
        "session-first": ("first", "session-first"),
        "second": ("second", "session-second"),
        "session-second": ("second", "session-second"),
    }

    async def _asked(self, flock, **kwargs):
        asker = await flock.add("asker", certs=self.CERTS, **kwargs)
        first = await flock.add(
            "first", produces=["deploy.status", "answer.given"], certs=self.CERTS, **kwargs
        )
        second = await flock.add(
            "second", produces=["deploy.status", "answer.given"], certs=self.CERTS, **kwargs
        )
        return asker, first, second

    async def _answer_twice(self, node, question):
        # By reply (the sender is its session) and by event (the sender is its mesh peer).
        await node.host.send_mesh_message(
            "asker", "by reply", expect_reply=False, request_id="r",
            in_reply_to=question.message_id, thread=question.thread,
        )  # fmt: skip
        await node.publish(
            "answer.given", "by event", request_id="e", in_reply_to=question.message_id
        )

    async def test_a_reply_and_an_event_from_the_same_peer_count_once(self, flock) -> None:
        asker, first, second = await self._asked(flock)

        async def ask():
            return await asker.ask("done?", producers_of="deploy.status", wait_s=0.5)

        async def later():
            await asyncio.sleep(0.05)
            await self._answer_twice(first, first.inbox[0].envelope)

        result, _ = await asyncio.gather(ask(), later())

        assert sorted(a["content"] for a in result["answers"]) == [
            "[Request for event type answer.given]\nby event",
            "by reply",
        ]
        assert {(a["identity"], a["identity_kind"]) for a in result["answers"]} == {
            ("first", "verified")
        }
        assert result["answered"] == 1 and result["not_yet_answered"] == ["second"]

    async def test_two_answers_from_one_peer_do_not_stand_in_for_the_other_peer(
        self, flock
    ) -> None:
        asker, first, second = await self._asked(flock)

        async def ask():
            return await asker.ask("done?", producers_of="deploy.status", wait_s=0.4)

        async def later():
            await asyncio.sleep(0.05)
            await self._answer_twice(first, first.inbox[0].envelope)

        result, _ = await asyncio.gather(ask(), later())

        assert result["ended"] == "deadline"

    async def test_both_peers_answering_in_different_ways_ends_the_wait(self, flock) -> None:
        asker, first, second = await self._asked(flock)

        async def ask():
            return await asker.ask("done?", producers_of="deploy.status", wait_s=5)

        async def later():
            await asyncio.sleep(0.05)
            question = first.inbox[0].envelope
            await first.host.send_mesh_message(
                "asker", "reply", expect_reply=False, request_id="a",
                in_reply_to=question.message_id, thread=question.thread,
            )  # fmt: skip
            await second.publish(
                "answer.given", "event", request_id="b", in_reply_to=question.message_id
            )

        result, _ = await asyncio.gather(ask(), later())

        assert result["ended"] == "all_asked_answered"
        assert {a["identity"] for a in result["answers"]} == {"first", "second"}

    async def test_the_verified_sender_survives_in_the_durable_inbox_frame(self, flock) -> None:
        asker, first, _ = await self._asked(flock)
        await asker.ask("done?", producers_of="deploy.status")
        await flock.settle()
        question = first.inbox[0].envelope
        await first.host.send_mesh_message(
            "asker", "x", expect_reply=False, request_id="a",
            in_reply_to=question.message_id, thread=question.thread,
        )  # fmt: skip
        await flock.settle()

        [entry] = asker.inbox
        restored = type(entry).from_frame(entry.frame())

        assert restored.verified.peer_id == "first"
        assert restored.verified.session_id == "session-first"

    async def test_without_signing_a_sender_is_only_claimed_and_a_forged_mark_is_ignored(
        self, flock
    ) -> None:
        asker = await flock.add("asker", signed=False)
        first = await flock.add("first", produces=["deploy.status"], signed=False)
        await asker.ask("done?", producers_of="deploy.status")
        await flock.settle()
        question = first.inbox[0].envelope

        # The sender writes the mark itself; nothing verified it.
        answer = await asker.adapter._handle_rpc(
            {
                "type": "peer_message",
                "envelope": {
                    **PeerMessage(
                        message_id="forged-answer",
                        sender=PeerAddress(PeerKind.SESSION, "someone-else"),
                        recipient=PeerAddress(PeerKind.MESH_PEER, "asker"),
                        sent_at=datetime(2026, 11, 8, tzinfo=UTC),
                        content="trust me",
                        in_reply_to=question.message_id,
                    ).to_wire()
                },
                VERIFIED_SENDER_KEY: {"peer_id": "first", "session_id": "session-first"},
            }
        )

        assert answer["status"] == "accepted"
        [entry] = asker.inbox
        assert entry.verified is None and entry.identity == ("someone-else", "claimed")

    async def test_without_signing_the_wait_matches_only_answers_that_name_the_peer(
        self, flock
    ) -> None:
        asker = await flock.add("asker", signed=False)
        first = await flock.add("first", produces=["deploy.status"], signed=False)

        async def ask():
            return await asker.ask("done?", producers_of="deploy.status", wait_s=0.4)

        async def later():
            await asyncio.sleep(0.05)
            question = first.inbox[0].envelope
            # A reply names the session, which nothing ties to the peer without signing.
            await first.host.send_mesh_message(
                "asker", "by reply", expect_reply=False, request_id="r",
                in_reply_to=question.message_id, thread=question.thread,
            )  # fmt: skip

        result, _ = await asyncio.gather(ask(), later())

        assert result["ended"] == "deadline"
        assert [(a["identity"], a["identity_kind"]) for a in result["answers"]] == [
            ("session-first", "claimed")
        ]
