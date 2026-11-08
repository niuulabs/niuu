"""Publishing to a mesh topic: declared producers and consumers, stamped sender, loud failures."""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.domain.peer_message import (
    PeerAddress,
    PeerKind,
    PeerMessage,
    ReplyKind,
    ReplyRoute,
)
from niuu.forge_mcp.models import ForgeApiError
from niuu.mesh.events import MeshEventType
from niuu.mesh.participant import MeshParticipant
from personas.domain.persona_document import PortablePersonaDefinition
from skuld.config import MeshConfig, SkuldSettings
from skuld.forge_mcp import host as host_module
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.mesh_adapter import SkuldMeshAdapter
from skuld.mesh_topics import (
    MeshTopics,
    UndeclaredTopicError,
    build_publication,
    mesh_topics,
    publication_event_id,
)
from skuld.peer_inbox import PEER_MESSAGES_COUNTER, PeerAcceptance
from sleipnir.adapters.in_process import InProcessBus
from tests.test_skuld.test_broker_review_regressions import _broker


def _persona(*, consumes=(), produces=None, persona_id="reviewer") -> dict:
    definition: dict = {"name": persona_id, "system_prompt_template": "You review code."}
    if consumes:
        definition["consumes"] = {"event_types": list(consumes)}
    if produces:
        definition["produces"] = produces
    return PortablePersonaDefinition(id=persona_id, revision="r1", definition=definition).to_dict()


def _settings(**overrides) -> SkuldSettings:
    persona = overrides.pop("persona", None)
    mesh = MeshConfig(enabled=True, peer_id="skuld-pub", **overrides)
    return SkuldSettings(mesh=mesh, session={"id": "s-1", "persona": persona})


class FakeInbox:
    """A consumer session's peer inbox, as its mesh adapter sees it."""

    def __init__(self) -> None:
        self.accepted: list[tuple[PeerMessage, str | None]] = []

    async def accept_peer_message(self, envelope, request_id=None, *, channel=None):
        self.accepted.append((envelope, channel))
        return PeerAcceptance(envelope.message_id, duplicate=False)


@pytest.fixture
def counted(monkeypatch):
    """Every ``skuld.peer.messages`` increment as its attributes."""
    calls: list[dict] = []
    telemetry = SimpleNamespace(
        count=lambda name, value=1, *, attributes=None, description="": calls.append(
            {"name": name, **(attributes or {})}
        ),
        inject=lambda: {},
    )
    monkeypatch.setattr(host_module, "get_observability", lambda: telemetry)
    return calls


class TestDeclaredTopics:
    def test_a_session_without_a_persona_declares_only_what_the_operator_listed(self) -> None:
        topics = mesh_topics(
            _settings(consumes_event_types=["code.requested"], publish_event_types=["build.done"])
        )

        assert topics == MeshTopics(
            consumes=("code.requested",), produces=("build.done", "help.needed")
        )

    def test_the_personas_consumes_are_subscribed_and_its_produces_may_be_published(self) -> None:
        persona = _persona(
            consumes=["review.requested", "code.requested"],
            produces={
                "event_type": "review.done",
                "event_type_map": {"fail": "review.changes_requested"},
            },
        )

        topics = mesh_topics(_settings(persona=persona, publish_event_types=["extra.topic"]))

        assert topics.consumes == ("code.requested", "review.requested")
        assert set(topics.produces) == {
            "extra.topic",
            "review.done",
            "review.changes_requested",
            "help.needed",
        }
        assert topics.persona_id == "reviewer"

    def test_a_persona_that_is_not_a_valid_document_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="schema_version"):
            mesh_topics(_settings(persona={"id": "x", "definition": {}}))

    def test_an_undeclared_topic_is_refused_naming_what_is_declared_and_the_remedy(self) -> None:
        topics = MeshTopics(consumes=(), produces=("review.done",), persona_id="reviewer")

        topics.check_publishable("review.done")
        with pytest.raises(UndeclaredTopicError) as excinfo:
            topics.check_publishable("review.dnoe")

        text = str(excinfo.value)
        assert "'review.dnoe'" in text and "'review.done'" in text and "'reviewer'" in text
        assert "produces" in text and "mesh.publish_event_types" in text

    def test_no_declaration_at_all_refuses_every_topic(self) -> None:
        with pytest.raises(UndeclaredTopicError, match="declared: none"):
            MeshTopics(consumes=(), produces=()).check_publishable("anything")


class TestPublication:
    def test_the_event_is_stamped_with_this_sessions_mesh_identity(self) -> None:
        event = build_publication(
            peer_id="skuld-pub",
            session_id="s-1",
            session_name="builder",
            persona_id="coder",
            topic="review.requested",
            content="please review",
            request_id="req-1",
            urgency=0.8,
            correlation_id="pr-12",
            in_reply_to="evt-0",
            now=datetime(2026, 11, 8, tzinfo=UTC),
        )

        assert event.type is MeshEventType.OUTCOME
        assert event.source == "skuld-pub"
        assert event.event_id == publication_event_id("skuld-pub", "review.requested", "req-1")
        assert event.correlation_id == "pr-12"
        assert event.payload["prompt"] == "please review"
        assert event.payload["event_type"] == "review.requested"
        assert event.payload["publisher"] == {
            "session_id": "s-1",
            "name": "builder",
            "node": socket.gethostname(),
            "peer_id": "skuld-pub",
        }
        assert event.payload["in_reply_to"] == "evt-0"

    def test_without_a_thread_tag_the_event_is_its_own_correlation(self) -> None:
        event = build_publication(
            peer_id="p",
            session_id="s",
            session_name="",
            persona_id="",
            topic="t",
            content="x",
            request_id="r",
            urgency=0.5,
        )

        assert event.correlation_id == event.event_id
        assert "correlation_id" not in event.payload and "in_reply_to" not in event.payload

    def test_a_retry_with_the_same_request_id_is_the_same_event(self) -> None:
        assert publication_event_id("p", "t", "r") == publication_event_id("p", "t", "r")
        assert publication_event_id("p", "t", "r") != publication_event_id("p", "t", "r2")
        assert publication_event_id("p", "t", "r") != publication_event_id("p2", "t", "r")


def _mesh_adapter(bus, peer_id, *, consumes, inbox=None) -> SkuldMeshAdapter:
    mesh = SleipnirMeshAdapter(
        publisher=bus, subscriber=bus, own_peer_id=peer_id, rpc_timeout_s=5.0
    )
    return SkuldMeshAdapter(
        participant=MeshParticipant(mesh=mesh, discovery=None, peer_id=peer_id),
        config=MeshConfig(enabled=True, peer_id=peer_id, rpc_timeout_s=5.0),
        session_id=f"session-of-{peer_id}",
        peer_inbox=inbox or FakeInbox(),
        consumes_event_types=consumes,
    )


def _host(tmp_path, adapter, *, produces=(), persona_id="builder", mesh_enabled=True):
    broker = _broker(tmp_path)
    broker._mesh_adapter = adapter
    broker._mesh_topics = (
        None if adapter is None else MeshTopics((), tuple(produces), persona_id=persona_id)
    )
    broker._settings.mesh = broker._settings.mesh.model_copy(update={"enabled": mesh_enabled})
    return BrokerForgeMcpHost(broker, client=SimpleNamespace()), broker


class TestPublishThroughTheMesh:
    async def test_it_goes_straight_onto_the_mesh_and_the_answer_is_the_transports(
        self, tmp_path, counted
    ) -> None:
        adapter = SimpleNamespace(peer_id="skuld-pub", publish_event=AsyncMock())
        host, broker = _host(tmp_path, adapter, produces=["review.requested"])
        broker._settings.session.name = "real-name"

        answer = await host.publish_mesh_event(
            "review.requested",
            "I am the CEO",
            request_id="req-1",
            correlation_id="",
            in_reply_to="",
        )

        event, topic = adapter.publish_event.await_args.args
        assert topic == "review.requested"
        assert answer == {
            "event_id": event.event_id,
            "topic": "review.requested",
            "correlation_id": event.event_id,
            "status": "published",
        }
        assert event.source == "skuld-pub"
        assert event.payload["publisher"]["name"] == "real-name"
        assert event.payload["publisher"]["session_id"] == str(broker.session_id)
        assert event.payload["prompt"] == "I am the CEO"
        assert counted == [
            {
                "name": PEER_MESSAGES_COUNTER,
                "sender": str(broker.session_id),
                "recipient": "review.requested",
                "channel": "mesh",
                "outcome": "published",
            }
        ]

    async def test_an_undeclared_topic_is_refused_loudly_and_nothing_is_published(
        self, tmp_path, counted
    ) -> None:
        adapter = SimpleNamespace(peer_id="skuld-pub", publish_event=AsyncMock())
        host, _ = _host(tmp_path, adapter, produces=["review.requested"])

        with pytest.raises(ForgeApiError, match="not a topic persona 'builder' declares") as exc:
            await host.publish_mesh_event(
                "review.requestd", "x", request_id="r", correlation_id="", in_reply_to=""
            )

        assert exc.value.status == 403
        assert "review.requested" in exc.value.detail
        adapter.publish_event.assert_not_awaited()
        assert [c["outcome"] for c in counted] == ["refused"]

    async def test_a_session_whose_mesh_did_not_start_raises_and_counts(
        self, tmp_path, counted
    ) -> None:
        host, _ = _host(tmp_path, None)

        with pytest.raises(ForgeApiError, match="mesh is not running") as exc:
            await host.publish_mesh_event(
                "t", "x", request_id="r", correlation_id="", in_reply_to=""
            )

        assert exc.value.status == 503
        assert [c["outcome"] for c in counted] == ["failed"]

    async def test_a_mesh_that_stopped_raises_instead_of_reading_as_published(
        self, tmp_path, counted
    ) -> None:
        bus = InProcessBus()
        adapter = _mesh_adapter(bus, "skuld-pub", consumes=())  # never started
        host, _ = _host(tmp_path, adapter, produces=["t"])

        with pytest.raises(ForgeApiError, match="did not accept the event on 't'") as exc:
            await host.publish_mesh_event(
                "t", "x", request_id="r", correlation_id="", in_reply_to=""
            )

        assert exc.value.status == 502 and "mesh is not running" in exc.value.detail
        assert [c["outcome"] for c in counted] == ["failed"]

    async def test_a_transport_that_refuses_the_event_raises_with_the_retry_remedy(
        self, tmp_path, counted
    ) -> None:
        adapter = SimpleNamespace(
            peer_id="skuld-pub", publish_event=AsyncMock(side_effect=ConnectionError("nats down"))
        )
        host, _ = _host(tmp_path, adapter, produces=["t"])

        with pytest.raises(ForgeApiError, match="nats down") as exc:
            await host.publish_mesh_event(
                "t", "x", request_id="r", correlation_id="", in_reply_to=""
            )

        assert exc.value.status == 502 and "same request_id" in exc.value.detail
        assert [c["outcome"] for c in counted] == ["failed"]


class TestAdapterPublish:
    async def test_a_stopped_adapter_raises(self) -> None:
        adapter = _mesh_adapter(InProcessBus(), "skuld-pub", consumes=())
        event = build_publication(
            peer_id="p",
            session_id="s",
            session_name="",
            persona_id="",
            topic="t",
            content="x",
            request_id="r",
            urgency=0.5,
        )

        with pytest.raises(RuntimeError, match="mesh is not running"):
            await adapter.publish_event(event, "t")

    async def test_the_adapter_subscribes_to_the_declared_topics_not_only_the_configs(
        self,
    ) -> None:
        mesh = SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            subscribe=AsyncMock(),
            unsubscribe=AsyncMock(),
            set_rpc_handler=lambda handler: None,
        )
        adapter = SkuldMeshAdapter(
            participant=MeshParticipant(mesh=mesh, discovery=None, peer_id="p"),
            config=MeshConfig(enabled=True, peer_id="p", consumes_event_types=["code.requested"]),
            session_id="s",
            peer_inbox=FakeInbox(),
            consumes_event_types=("code.requested", "review.requested"),
        )

        await adapter.start()
        await adapter.stop()

        assert [call.args[0] for call in mesh.subscribe.await_args_list] == [
            "code.requested",
            "review.requested",
            adapter.inbox_topic,
        ]
        assert [call.args[0] for call in mesh.unsubscribe.await_args_list] == [
            "code.requested",
            "review.requested",
            adapter.inbox_topic,
        ]


class TestFanOutAndLateAnswers:
    """One test mesh, one publisher and two consumers declared to listen."""

    async def _mesh(self, tmp_path):
        bus = InProcessBus()
        publisher_inbox, one_inbox, two_inbox = FakeInbox(), FakeInbox(), FakeInbox()
        publisher = _mesh_adapter(
            bus, "skuld-pub", consumes=("review.done",), inbox=publisher_inbox
        )
        one = _mesh_adapter(bus, "reviewer-1", consumes=("review.requested",), inbox=one_inbox)
        two = _mesh_adapter(bus, "reviewer-2", consumes=("review.requested",), inbox=two_inbox)
        bystander_inbox = FakeInbox()
        bystander = _mesh_adapter(
            bus, "bystander", consumes=("other.topic",), inbox=bystander_inbox
        )
        adapters = [publisher, one, two, bystander]
        for adapter in adapters:
            await adapter.start()
        host, broker = _host(tmp_path, publisher, produces=["review.requested"])
        return SimpleNamespace(
            bus=bus,
            host=host,
            broker=broker,
            publisher=publisher,
            one=one,
            two=two,
            inboxes=SimpleNamespace(
                publisher=publisher_inbox, one=one_inbox, two=two_inbox, bystander=bystander_inbox
            ),
            adapters=adapters,
        )

    async def _settle(self, mesh) -> None:
        await mesh.bus.flush()

    async def _stop(self, mesh) -> None:
        for adapter in mesh.adapters:
            await adapter.stop()

    async def test_every_declared_consumer_gets_it_and_nobody_else(self, tmp_path) -> None:
        mesh = await self._mesh(tmp_path)
        try:
            answer = await mesh.host.publish_mesh_event(
                "review.requested",
                "please review PR 12",
                request_id="req-1",
                correlation_id="pr-12",
                in_reply_to="",
            )
            await self._settle(mesh)

            for inbox in (mesh.inboxes.one, mesh.inboxes.two):
                [(envelope, channel)] = inbox.accepted
                assert channel == "mesh"
                assert envelope.message_id == answer["event_id"]
                assert envelope.sender == PeerAddress(
                    PeerKind.MESH_PEER, "skuld-pub", name="skuld-pub"
                )
                assert envelope.reply == ReplyRoute(ReplyKind.MESH, "skuld-pub")
                assert envelope.thread == "pr-12"
                assert "please review PR 12" in envelope.content
                assert envelope.in_reply_to == ""
            assert mesh.inboxes.bystander.accepted == []
            assert mesh.inboxes.publisher.accepted == []
        finally:
            await self._stop(mesh)

    async def test_a_consumer_answers_later_with_a_reply_linked_to_the_event(
        self, tmp_path
    ) -> None:
        mesh = await self._mesh(tmp_path)
        try:
            published = await mesh.host.publish_mesh_event(
                "review.requested",
                "please review",
                request_id="r",
                correlation_id="",
                in_reply_to="",
            )
            await self._settle(mesh)
            [(received, _)] = mesh.inboxes.one.accepted

            # The consumer answers on the reply route it was given: the mesh, to the publisher.
            answer = PeerMessage(
                message_id="answer-1",
                sender=PeerAddress(PeerKind.MESH_PEER, "reviewer-1"),
                recipient=PeerAddress(PeerKind.MESH_PEER, received.reply.target),
                sent_at=datetime.now(UTC),
                content="make these changes: rename x",
                in_reply_to=received.message_id,
            )
            result = await mesh.one.send_peer_message(received.reply.target, answer)

            assert result["status"] == "accepted"
            [(delivered, channel)] = mesh.inboxes.publisher.accepted
            assert channel == "mesh"
            assert delivered.in_reply_to == published["event_id"]
            assert delivered.content == "make these changes: rename x"
        finally:
            await self._stop(mesh)

    async def test_a_consumer_answers_later_by_publishing_with_the_event_id(self, tmp_path) -> None:
        mesh = await self._mesh(tmp_path)
        try:
            published = await mesh.host.publish_mesh_event(
                "review.requested",
                "please review",
                request_id="r",
                correlation_id="",
                in_reply_to="",
            )
            await self._settle(mesh)
            reviewer_host, _ = _host(
                tmp_path, mesh.two, produces=["review.done"], persona_id="reviewer"
            )

            await reviewer_host.publish_mesh_event(
                "review.done",
                "approved with comments",
                request_id="done-1",
                correlation_id="",
                in_reply_to=published["event_id"],
            )
            await self._settle(mesh)

            [(delivered, _)] = mesh.inboxes.publisher.accepted
            assert delivered.in_reply_to == published["event_id"]
            assert delivered.sender.id == "reviewer-2"
            assert "approved with comments" in delivered.content
            assert delivered.reply == ReplyRoute(ReplyKind.MESH, "reviewer-2")
        finally:
            await self._stop(mesh)

    async def test_a_retry_with_the_same_request_id_is_one_event_for_the_consumers(
        self, tmp_path
    ) -> None:
        mesh = await self._mesh(tmp_path)
        try:
            first = await mesh.host.publish_mesh_event(
                "review.requested", "x", request_id="same", correlation_id="", in_reply_to=""
            )
            second = await mesh.host.publish_mesh_event(
                "review.requested", "x", request_id="same", correlation_id="", in_reply_to=""
            )
            await self._settle(mesh)

            assert first["event_id"] == second["event_id"]
            ids = {envelope.message_id for envelope, _ in mesh.inboxes.one.accepted}
            assert ids == {first["event_id"]}
        finally:
            await self._stop(mesh)

    async def test_a_topic_nobody_declared_never_reaches_the_mesh(self, tmp_path) -> None:
        mesh = await self._mesh(tmp_path)
        try:
            with pytest.raises(ForgeApiError, match="declares it publishes"):
                await mesh.host.publish_mesh_event(
                    "other.topic", "x", request_id="r", correlation_id="", in_reply_to=""
                )
            await self._settle(mesh)

            assert mesh.inboxes.bystander.accepted == []
        finally:
            await self._stop(mesh)


class TestConsumerEnvelope:
    async def test_an_event_without_a_link_carries_none(self) -> None:
        inbox = FakeInbox()
        adapter = _mesh_adapter(InProcessBus(), "c", consumes=(), inbox=inbox)
        event = build_publication(
            peer_id="pub",
            session_id="s",
            session_name="",
            persona_id="",
            topic="t",
            content="work",
            request_id="r",
            urgency=0.5,
        )

        await adapter._handle_outcome_event(event)

        [(envelope, _)] = inbox.accepted
        assert envelope.in_reply_to == "" and envelope.thread == event.event_id
        assert envelope.content == "[Request for event type t]\nwork"
        assert envelope.recipient == PeerAddress(PeerKind.SESSION, "session-of-c")
        assert envelope.reply == ReplyRoute(ReplyKind.MESH, "pub")


class TestBrokerWiring:
    async def test_the_personas_declaration_drives_the_subscriptions_and_the_publishable_topics(
        self, tmp_path
    ) -> None:
        broker = _broker(tmp_path)
        broker._settings.mesh = MeshConfig(
            enabled=True,
            peer_id="wired",
            transport="in_process",
            rpc_timeout_s=5.0,
            consumes_event_types=["code.requested"],
        )
        broker._settings.session.persona = _persona(
            consumes=["review.requested"], produces={"event_type": "review.done"}
        )
        broker._room_bridge = None

        await broker._start_mesh_adapter()
        try:
            assert broker._mesh_adapter._consumes == ("code.requested", "review.requested")
            assert broker._mesh_topics.produces == ("review.done", "help.needed")
            host = BrokerForgeMcpHost(broker, client=SimpleNamespace())
            answer = await host.publish_mesh_event(
                "review.done", "ok", request_id="r", correlation_id="", in_reply_to=""
            )
            assert answer["status"] == "published"
            with pytest.raises(ForgeApiError, match="declares it publishes"):
                await host.publish_mesh_event(
                    "review.requested", "ok", request_id="r", correlation_id="", in_reply_to=""
                )
        finally:
            await broker._mesh_adapter.stop()
