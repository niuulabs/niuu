"""Tests for Skuld mesh adapter (NIU-612).

Covers:
- SkuldMeshAdapter lifecycle (start/stop)
- Config parsing (mesh.enabled true/false)
- peer_message RPC, work_request RPC and outcome events landing in the peer inbox
- Refusals and malformed envelopes answered loudly
- Integration with InProcessBus round-trip
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from niuu.domain.peer_message import (
    PEER_MESSAGE_FRAME,
    PeerAddress,
    PeerKind,
    PeerMessage,
    ReplyKind,
    ReplyRoute,
)
from niuu.mesh.events import MeshEvent, MeshEventType
from skuld.config import MeshConfig, SkuldSettings
from skuld.mesh_adapter import SkuldMeshAdapter
from skuld.peer_inbox import PeerAcceptance, PeerMessageRefusedError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mesh_config():
    return MeshConfig(
        enabled=True,
        peer_id="skuld-test-01",
        capabilities=["coding", "git"],
        persona="coder",
        transport="in_process",
        consumes_event_types=["code.requested"],
        rpc_timeout_s=5.0,
    )


@pytest.fixture
def disabled_mesh_config():
    return MeshConfig(enabled=False)


@pytest.fixture
def mock_mesh():
    mesh = MagicMock()
    mesh.start = AsyncMock()
    mesh.stop = AsyncMock()
    mesh.publish = AsyncMock()
    mesh.subscribe = AsyncMock()
    mesh.unsubscribe = AsyncMock()
    mesh.set_rpc_handler = MagicMock()
    return mesh


class FakeInbox:
    """The session's peer inbox, as the mesh adapter sees it."""

    def __init__(self) -> None:
        self.accepted: list[tuple[PeerMessage, str | None]] = []
        self.refuse_with: str | None = None
        self.duplicate_ids: set[str] = set()

    async def accept_peer_message(self, envelope, request_id=None, *, channel=None):
        if self.refuse_with is not None:
            raise PeerMessageRefusedError(self.refuse_with)
        duplicate = envelope.message_id in self.duplicate_ids
        self.accepted.append((envelope, channel))
        return PeerAcceptance(envelope.message_id, duplicate=duplicate)


@pytest.fixture
def inbox():
    return FakeInbox()


@pytest.fixture
def adapter(mock_mesh, inbox, mesh_config):
    from niuu.mesh.participant import MeshParticipant

    participant = MeshParticipant(mesh=mock_mesh, discovery=None, peer_id=mesh_config.peer_id)
    return SkuldMeshAdapter(
        participant=participant,
        peer_inbox=inbox,
        config=mesh_config,
        session_id="test-session-42",
    )


# ---------------------------------------------------------------------------
# Config parsing tests
# ---------------------------------------------------------------------------


class TestMeshConfig:
    """Test mesh configuration parsing."""

    @pytest.fixture(autouse=True)
    def _no_yaml_config(self, monkeypatch):
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])

    def test_mesh_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("SKULD__MESH__ENABLED", raising=False)
        s = SkuldSettings()
        assert s.mesh.enabled is False
        assert s.mesh.peer_id == ""
        assert s.mesh.persona == "coder"

    def test_mesh_enabled_via_constructor(self):
        s = SkuldSettings(mesh={"enabled": True, "peer_id": "my-skuld"})
        assert s.mesh.enabled is True
        assert s.mesh.peer_id == "my-skuld"

    def test_mesh_default_capabilities(self):
        cfg = MeshConfig()
        assert "coding" in cfg.capabilities
        assert "git" in cfg.capabilities
        assert "terminal" in cfg.capabilities
        assert "file_edit" in cfg.capabilities

    def test_mesh_default_consumes(self):
        cfg = MeshConfig()
        assert cfg.consumes_event_types == ["code.requested"]

    def test_mesh_custom_capabilities(self):
        cfg = MeshConfig(capabilities=["review"])
        assert cfg.capabilities == ["review"]

    def test_mesh_transport_default(self):
        cfg = MeshConfig()
        assert cfg.transport == "nng"

    def test_mesh_rpc_timeout(self):
        cfg = MeshConfig(rpc_timeout_s=30.0)
        assert cfg.rpc_timeout_s == 30.0


class TestSkuldMeshAdapterLifecycle:
    """Test start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_starts_mesh_and_subscribes(self, adapter, mock_mesh):
        await adapter.start()

        mock_mesh.start.assert_awaited_once()
        # The standing topic, and the inbox topic answers to this peer arrive on.
        assert [call.args for call in mock_mesh.subscribe.await_args_list] == [
            ("code.requested", adapter._handle_outcome_event),
            (adapter.inbox_topic, adapter._handle_outcome_event),
        ]
        assert adapter.is_running is True

    @pytest.mark.asyncio
    async def test_start_registers_rpc_handler(self, adapter, mock_mesh):
        await adapter.start()

        mock_mesh.set_rpc_handler.assert_called_once_with(adapter._handle_rpc)

    @pytest.mark.asyncio
    async def test_start_with_discovery(self, mock_mesh, inbox, mesh_config):
        from niuu.mesh.participant import MeshParticipant

        discovery = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        participant = MeshParticipant(mesh=mock_mesh, discovery=discovery, peer_id="s1")
        adapter = SkuldMeshAdapter(
            participant=participant,
            peer_inbox=inbox,
            config=mesh_config,
            session_id="s1",
        )
        await adapter.start()

        discovery.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_unsubscribes_and_stops(self, adapter, mock_mesh):
        await adapter.start()
        await adapter.stop()

        assert [call.args[0] for call in mock_mesh.unsubscribe.await_args_list] == [
            "code.requested",
            adapter.inbox_topic,
        ]
        mock_mesh.stop.assert_awaited_once()
        assert adapter.is_running is False

    @pytest.mark.asyncio
    async def test_stop_with_discovery(self, mock_mesh, inbox, mesh_config):
        from niuu.mesh.participant import MeshParticipant

        discovery = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        participant = MeshParticipant(mesh=mock_mesh, discovery=discovery, peer_id="s1")
        adapter = SkuldMeshAdapter(
            participant=participant,
            peer_inbox=inbox,
            config=mesh_config,
            session_id="s1",
        )
        await adapter.start()
        await adapter.stop()

        discovery.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self, adapter, mock_mesh):
        await adapter.start()
        await adapter.start()

        assert mock_mesh.start.await_count == 1

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_noop(self, adapter, mock_mesh):
        await adapter.stop()
        mock_mesh.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_peer_id_from_config(self, adapter):
        assert adapter.peer_id == "skuld-test-01"

    @pytest.mark.asyncio
    async def test_peer_id_defaults_to_hostname(self, mock_mesh, inbox):
        import socket

        from niuu.mesh.participant import MeshParticipant

        cfg = MeshConfig(enabled=True, peer_id="")
        participant = MeshParticipant(mesh=mock_mesh, discovery=None, peer_id="")
        adapter = SkuldMeshAdapter(
            participant=participant,
            peer_inbox=inbox,
            config=cfg,
            session_id="s1",
        )
        assert adapter.peer_id == socket.gethostname()

    @pytest.mark.asyncio
    async def test_publish_forwards_event_to_mesh(self, adapter, mock_mesh):
        event = MeshEvent(
            type=MeshEventType.OUTCOME,
            source="skuld:test",
            payload={"event_type": "code.requested"},
            timestamp=datetime.now(UTC),
            urgency=0.5,
            correlation_id="corr-1",
            session_id="session-1",
        )

        await adapter.publish(event, "code.requested")

        mock_mesh.publish.assert_awaited_once_with(event, topic="code.requested")

    @pytest.mark.asyncio
    async def test_multiple_event_type_subscriptions(self, mock_mesh, inbox):
        from niuu.mesh.participant import MeshParticipant

        cfg = MeshConfig(
            enabled=True,
            peer_id="s1",
            consumes_event_types=["code.requested", "review.requested"],
        )
        participant = MeshParticipant(mesh=mock_mesh, discovery=None, peer_id="s1")
        adapter = SkuldMeshAdapter(
            participant=participant,
            peer_inbox=inbox,
            config=cfg,
            session_id="s1",
        )
        await adapter.start()

        assert mock_mesh.subscribe.await_count == 3  # two declared topics and the inbox topic
        await adapter.stop()
        assert mock_mesh.unsubscribe.await_count == 3


# ---------------------------------------------------------------------------
# RPC tests
# ---------------------------------------------------------------------------


def _envelope(**changes) -> PeerMessage:
    fields = {
        "message_id": "msg-1",
        "sender": PeerAddress(PeerKind.SESSION, "sender-session", name="planner"),
        "recipient": PeerAddress(PeerKind.SESSION, "test-session-42"),
        "sent_at": datetime(2026, 11, 8, 10, 0, tzinfo=UTC),
        "content": "Please review the diff",
        "reply": ReplyRoute(ReplyKind.SESSION, "sender-session"),
    }
    return PeerMessage(**{**fields, **changes})


class TestRpcHandling:
    """RPCs a mesh peer may send."""

    @pytest.mark.asyncio
    async def test_directed_message_preserves_room_correlation(self, adapter, mock_mesh):
        mock_mesh.send = AsyncMock(return_value={"status": "accepted"})

        result = await adapter.send_directed_message(
            "reviewer",
            "Review this",
            metadata={
                "session_id": "test-session-42",
                "root_correlation_id": "test-session-42",
                "trace_context": {"traceparent": "00-a-b-01"},
            },
        )

        assert result == {"status": "accepted"}
        mock_mesh.send.assert_awaited_once_with(
            "reviewer",
            {
                "type": "directed_message",
                "content": "Review this",
                "metadata": {
                    "session_id": "test-session-42",
                    "root_correlation_id": "test-session-42",
                    "trace_context": {"traceparent": "00-a-b-01"},
                },
            },
            timeout_s=5.0,
        )

    @pytest.mark.asyncio
    async def test_unknown_rpc_type_returns_error(self, adapter):
        await adapter.start()

        result = await adapter._handle_rpc({"type": "unknown_type"})
        assert result["error"] == "unknown_message_type"

    @pytest.mark.asyncio
    async def test_registered_rpc_types_reach_their_handler(self, mock_mesh, inbox, mesh_config):
        from niuu.mesh.participant import MeshParticipant

        seen: list[dict] = []

        async def present(message: dict) -> dict:
            seen.append(message)
            return {"status": "complete", "file_id": "pf_1"}

        adapter = SkuldMeshAdapter(
            participant=MeshParticipant(
                mesh=mock_mesh, discovery=None, peer_id=mesh_config.peer_id
            ),
            peer_inbox=inbox,
            config=mesh_config,
            session_id="test-session-42",
            rpc_handlers={"present_file": present},
        )
        await adapter.start()

        message = {"type": "present_file", "path": "docs/plan.md"}
        assert await adapter._handle_rpc(message) == {"status": "complete", "file_id": "pf_1"}
        assert seen == [message]
        assert (await adapter._handle_rpc({"type": "other"}))["error"] == "unknown_message_type"

    @pytest.mark.asyncio
    async def test_peer_message_rpc_puts_the_envelope_in_the_inbox(self, adapter, inbox):
        envelope = _envelope()

        result = await adapter._handle_rpc(
            {"type": PEER_MESSAGE_FRAME, "envelope": envelope.to_wire()}
        )

        assert result == {"status": "accepted", "message_id": "msg-1"}
        assert inbox.accepted == [(envelope, "mesh")]

    @pytest.mark.asyncio
    async def test_a_duplicate_peer_message_is_accepted_and_flagged(self, adapter, inbox):
        inbox.duplicate_ids.add("msg-1")

        result = await adapter._handle_rpc(
            {"type": PEER_MESSAGE_FRAME, "envelope": _envelope().to_wire()}
        )

        assert result == {"status": "accepted", "message_id": "msg-1", "duplicate": True}

    @pytest.mark.asyncio
    async def test_a_malformed_envelope_is_refused_with_its_reason(self, adapter, inbox):
        wire = _envelope().to_wire()
        wire["version"] = 99

        result = await adapter._handle_rpc({"type": PEER_MESSAGE_FRAME, "envelope": wire})

        assert result["status"] == "refused"
        assert "unsupported peer message version 99" in result["error"]
        assert inbox.accepted == []

    @pytest.mark.asyncio
    async def test_a_missing_envelope_is_refused(self, adapter, inbox):
        result = await adapter._handle_rpc({"type": PEER_MESSAGE_FRAME})

        assert result == {"status": "refused", "error": "a peer message must be an object"}

    @pytest.mark.asyncio
    async def test_the_inboxs_refusal_reaches_the_sender_with_its_reason(self, adapter, inbox):
        inbox.refuse_with = "the message is addressed to someone else"

        result = await adapter._handle_rpc(
            {"type": PEER_MESSAGE_FRAME, "envelope": _envelope().to_wire()}
        )

        assert result == {
            "status": "refused",
            "message_id": "msg-1",
            "error": "the message is addressed to someone else",
        }

    @pytest.mark.asyncio
    async def test_work_request_becomes_a_message_from_the_requesting_peer(self, adapter, inbox):
        result = await adapter._handle_rpc(
            {
                "type": "work_request",
                "prompt": "Implement the retry",
                "event_type": "code.requested",
                "request_id": "req-9",
                "source_peer_id": "ravn-7",
            }
        )

        assert result == {"status": "accepted", "message_id": "req-9", "request_id": "req-9"}
        ((envelope, channel),) = inbox.accepted
        assert channel == "mesh"
        assert envelope.message_id == "req-9"
        assert envelope.sender == PeerAddress(PeerKind.MESH_PEER, "ravn-7", name="ravn-7")
        assert envelope.recipient == PeerAddress(PeerKind.SESSION, "test-session-42")
        assert envelope.reply == ReplyRoute(ReplyKind.MESH, "ravn-7")
        assert envelope.content == "[Request for event type code.requested]\nImplement the retry"

    @pytest.mark.asyncio
    async def test_work_request_without_an_event_type_carries_the_bare_prompt(self, adapter, inbox):
        await adapter._handle_work_request(
            {"prompt": "Do it", "request_id": "req-1", "source_peer_id": "ravn-7"}
        )

        assert inbox.accepted[0][0].content == "Do it"

    @pytest.mark.asyncio
    async def test_work_request_without_a_request_id_is_given_one(self, adapter, inbox):
        result = await adapter._handle_work_request({"prompt": "Do it", "source_peer_id": "ravn-7"})

        assert result["status"] == "accepted"
        assert result["request_id"] == result["message_id"] != ""

    @pytest.mark.asyncio
    async def test_work_request_empty_prompt_returns_error(self, adapter, inbox):
        result = await adapter._handle_rpc(
            {"type": "work_request", "request_id": "r1", "source_peer_id": "ravn-7"}
        )

        assert result == {"status": "error", "request_id": "r1", "error": "empty prompt"}
        assert inbox.accepted == []

    @pytest.mark.asyncio
    async def test_work_request_without_a_source_is_an_error_naming_why(self, adapter, inbox):
        result = await adapter._handle_rpc(
            {"type": "work_request", "prompt": "Do it", "request_id": "r1"}
        )

        assert result["status"] == "error"
        assert "needs source_peer_id" in result["error"]
        assert inbox.accepted == []

    @pytest.mark.asyncio
    async def test_a_refused_work_request_says_so(self, adapter, inbox):
        inbox.refuse_with = "this session does not take peer messages"

        result = await adapter._handle_work_request(
            {"prompt": "Do it", "request_id": "r1", "source_peer_id": "ravn-7"}
        )

        assert result == {
            "status": "refused",
            "message_id": "r1",
            "error": "this session does not take peer messages",
            "request_id": "r1",
        }

    @pytest.mark.asyncio
    async def test_a_work_request_the_envelope_rejects_is_an_error(self, adapter, inbox):
        result = await adapter._handle_work_request(
            {"prompt": "   ", "request_id": "r1", "source_peer_id": "ravn-7"}
        )

        assert result["status"] == "error"
        assert "needs content" in result["error"]


# ---------------------------------------------------------------------------
# Outcome event subscription tests
# ---------------------------------------------------------------------------


class TestOutcomeEventHandling:
    """Outcome events on consumed topics become messages in the inbox."""

    @staticmethod
    def _event(payload: dict, *, type=MeshEventType.OUTCOME) -> MeshEvent:
        return MeshEvent(
            type=type,
            source="other-ravn",
            payload=payload,
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="c1",
            session_id="s1",
        )

    @pytest.mark.asyncio
    async def test_non_outcome_event_ignored(self, adapter, inbox):
        await adapter._handle_outcome_event(
            self._event({"text": "thinking..."}, type=MeshEventType.THOUGHT)
        )

        assert inbox.accepted == []

    @pytest.mark.asyncio
    async def test_outcome_without_prompt_ignored(self, adapter, inbox):
        await adapter._handle_outcome_event(self._event({"event_type": "code.requested"}))

        assert inbox.accepted == []
        adapter._mesh.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_outcome_with_prompt_lands_in_the_inbox_and_publishes_nothing(
        self, adapter, inbox
    ):
        event = self._event({"prompt": "Fix the bug", "event_type": "code.requested"})

        await adapter._handle_outcome_event(event)

        ((envelope, channel),) = inbox.accepted
        assert channel == "mesh"
        assert envelope.message_id == event.event_id
        assert envelope.sender == PeerAddress(PeerKind.MESH_PEER, "other-ravn", name="other-ravn")
        assert envelope.reply == ReplyRoute(ReplyKind.MESH, "other-ravn")
        assert envelope.content == "[Request for event type code.requested]\nFix the bug"
        adapter._mesh.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_refused_outcome_event_raises_naming_the_reason(self, adapter, inbox):
        inbox.refuse_with = "this session has no durable Forge log"
        event = self._event({"prompt": "Fix the bug", "event_type": "code.requested"})

        with pytest.raises(PeerMessageRefusedError, match="has no durable Forge log"):
            await adapter._handle_outcome_event(event)


# ---------------------------------------------------------------------------
# Integration: InProcessBus round-trip
# ---------------------------------------------------------------------------


class TestMeshRoundTrip:
    """Integration test using InProcessBus — no real CLI needed."""

    @pytest.mark.asyncio
    async def test_peer_message_rpc_round_trip_with_in_process_bus(self):
        """A peer sends a peer_message over mesh RPC and gets the inbox's answer."""
        from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
        from niuu.mesh.participant import MeshParticipant
        from sleipnir.adapters.in_process import InProcessBus

        bus = InProcessBus()
        receiver = SleipnirMeshAdapter(
            publisher=bus, subscriber=bus, own_peer_id="skuld-integration", rpc_timeout_s=5.0
        )
        sender = SleipnirMeshAdapter(
            publisher=bus, subscriber=bus, own_peer_id="ravn-sender", rpc_timeout_s=5.0
        )
        inbox = FakeInbox()
        adapter = SkuldMeshAdapter(
            participant=MeshParticipant(mesh=receiver, discovery=None, peer_id="skuld-integration"),
            config=MeshConfig(enabled=True, peer_id="skuld-integration"),
            session_id="int-session",
            peer_inbox=inbox,
        )
        await adapter.start()
        await sender.start()
        envelope = _envelope(recipient=PeerAddress(PeerKind.SESSION, "int-session"))

        answer = await sender.send(
            "skuld-integration", {"type": PEER_MESSAGE_FRAME, "envelope": envelope.to_wire()}
        )

        assert answer == {"status": "accepted", "message_id": "msg-1"}
        assert inbox.accepted == [(envelope, "mesh")]
        await sender.stop()
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_publish_subscribe_round_trip(self):
        """Subscribe to topic, publish event, verify handler fires."""
        from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
        from sleipnir.adapters.in_process import InProcessBus

        bus = InProcessBus()
        mesh = SleipnirMeshAdapter(
            publisher=bus,
            subscriber=bus,
            own_peer_id="skuld-pubsub",
            rpc_timeout_s=5.0,
        )

        received: list[MeshEvent] = []

        async def handler(event: MeshEvent) -> None:
            received.append(event)

        await mesh.start()
        await mesh.subscribe("test.topic", handler)

        event = MeshEvent(
            type=MeshEventType.OUTCOME,
            source="test-source",
            payload={"event_type": "test.topic", "data": "hello"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="c1",
            session_id="s1",
        )

        await mesh.publish(event, topic="test.topic")
        await bus.flush()

        # Give async handler a chance to run
        await asyncio.sleep(0.1)

        assert len(received) >= 1
        assert received[0].payload["data"] == "hello"

        await mesh.stop()


# ---------------------------------------------------------------------------
# Shared niuu.mesh builder tests
# ---------------------------------------------------------------------------


class TestNiuuMeshBuilder:
    """Test shared mesh builder in niuu.mesh."""

    def test_resolve_peer_id_with_value(self):
        from niuu.mesh import resolve_peer_id

        assert resolve_peer_id("my-peer") == "my-peer"

    def test_resolve_peer_id_empty_falls_back_to_hostname(self):
        import socket

        from niuu.mesh import resolve_peer_id

        assert resolve_peer_id("") == socket.gethostname()

    def test_build_in_process_mesh(self):
        from niuu.mesh import build_in_process_mesh

        mesh = build_in_process_mesh("test-peer", rpc_timeout_s=5.0, rpc_reply_cache_size=4)
        assert mesh is not None
        assert mesh._rpc_reply_cache_size == 4

    def test_build_mesh_from_adapters_list_empty_raises(self):
        from niuu.mesh import MeshBuildError, build_mesh_from_adapters_list

        with pytest.raises(MeshBuildError, match="no mesh adapters configured"):
            build_mesh_from_adapters_list(
                adapters=[],
                own_peer_id="test",
                rpc_timeout_s=5.0,
                rpc_reply_cache_size=4,
            )

    def test_build_mesh_from_adapters_list_bad_import_raises(self):
        from niuu.mesh import MeshBuildError, build_mesh_from_adapters_list

        with pytest.raises(MeshBuildError, match="could not be imported") as excinfo:
            build_mesh_from_adapters_list(
                adapters=[{"adapter": "nonexistent.module.Class"}],
                own_peer_id="test",
                rpc_timeout_s=5.0,
                rpc_reply_cache_size=4,
            )

        assert isinstance(excinfo.value.__cause__, ImportError)

    def test_build_mesh_from_adapters_list_missing_adapter_key_raises(self):
        from niuu.mesh import MeshBuildError, build_mesh_from_adapters_list

        with pytest.raises(MeshBuildError, match=r"mesh.adapters\[0\] has no 'adapter' key"):
            build_mesh_from_adapters_list(
                adapters=[{"not_adapter": "foo"}],
                own_peer_id="test",
                rpc_timeout_s=5.0,
                rpc_reply_cache_size=4,
            )

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            ({"adapter": "sleipnir", "transport": "in_process"}, 4),
            ({"adapter": "sleipnir", "transport": "in_process", "rpc_reply_cache_size": 9}, 9),
        ],
    )
    def test_build_mesh_from_adapters_list_sets_sleipnir_reply_cache_size(self, entry, expected):
        """The configured size applies to Sleipnir entries unless the entry sets its own."""
        from niuu.mesh import build_mesh_from_adapters_list
        from sleipnir.adapters.in_process import InProcessBus

        mesh = build_mesh_from_adapters_list(
            adapters=[entry],
            own_peer_id="test",
            rpc_timeout_s=5.0,
            rpc_reply_cache_size=4,
            sleipnir_transport_builder=lambda _entry: InProcessBus(),
        )

        assert mesh._rpc_reply_cache_size == expected

    def test_mesh_aliases_resolve(self):
        from niuu.mesh import MESH_ALIASES

        assert "sleipnir" in MESH_ALIASES
        assert "webhook" in MESH_ALIASES
        assert "SleipnirMeshAdapter" in MESH_ALIASES["sleipnir"]


# ---------------------------------------------------------------------------
# Broker integration: mesh disabled → no adapter
# ---------------------------------------------------------------------------


class TestBrokerMeshIntegration:
    """Test mesh adapter integration in Broker."""

    @pytest.fixture(autouse=True)
    def _no_yaml_config(self, monkeypatch):
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])

    def test_mesh_disabled_no_adapter_created(self, tmp_path):
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            mesh={"enabled": False},
        )
        b = Broker(settings=settings)
        assert b._mesh_adapter is None

    def test_mesh_enabled_field_initially_none(self, tmp_path):
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            mesh={"enabled": True, "peer_id": "test-skuld"},
        )
        b = Broker(settings=settings)
        # Before startup, mesh_adapter is None
        assert b._mesh_adapter is None

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_integrates(self, tmp_path):
        """Test _start_mesh_adapter with in_process transport."""
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={
                "enabled": True,
                "peer_id": "test-skuld",
                "transport": "in_process",
                "rpc_reply_cache_size": 3,
            },
        )
        b = Broker(settings=settings)

        transport = MagicMock()
        _cb_holder: dict[str, object] = {"cb": None}

        def on_event(cb):
            _cb_holder["cb"] = cb

        transport.on_event = on_event
        type(transport).event_callback = property(lambda self: _cb_holder["cb"])
        b._transport = transport

        await b._start_mesh_adapter()

        assert b._mesh_adapter is not None
        assert b._mesh_adapter.is_running is True
        assert b._mesh_adapter._mesh._rpc_reply_cache_size == 3

        await b._mesh_adapter.stop()

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_nng_builds_sleipnir(self, tmp_path):
        """_start_mesh_adapter with nng transport builds SleipnirMeshAdapter (NIU-634)."""
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={
                "enabled": True,
                "peer_id": "test-peer",
                "transport": "nng",
                "rpc_reply_cache_size": 3,
            },
        )
        b = Broker(settings=settings)
        b._transport = MagicMock()

        mock_nng = MagicMock()
        mock_mesh = MagicMock()
        mock_mesh.start = AsyncMock()
        mock_mesh.stop = AsyncMock()
        mock_mesh.subscribe = AsyncMock()
        mock_mesh.unsubscribe = AsyncMock()

        with (
            patch(
                "niuu.mesh.transport_builder.build_nng_transport",
                return_value=mock_nng,
            ),
            patch(
                "niuu.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter",
                return_value=mock_mesh,
            ) as mock_mesh_cls,
            patch("skuld.broker.build_discovery_adapters", return_value=None),
            patch("niuu.mesh.cluster.read_cluster_pub_addresses", return_value=[]),
        ):
            await b._start_mesh_adapter()

        assert b._mesh_adapter is not None
        assert b._mesh_adapter.is_running is True
        assert mock_mesh_cls.call_args.kwargs["rpc_reply_cache_size"] == 3
        await b._mesh_adapter.stop()

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_adapters_list_passes_reply_cache_size(self, tmp_path):
        """The adapters-list path hands the configured reply cache size to the builder."""
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={
                "enabled": True,
                "peer_id": "test-peer",
                "adapters": [{"adapter": "sleipnir", "transport": "nats"}],
                "discovery_adapters": [{"adapter": "static"}],
                "rpc_reply_cache_size": 3,
            },
        )
        b = Broker(settings=settings)
        b._transport = MagicMock()

        mock_mesh = MagicMock()
        mock_mesh.start = AsyncMock()
        mock_mesh.stop = AsyncMock()
        mock_mesh.subscribe = AsyncMock()
        mock_mesh.unsubscribe = AsyncMock()

        with (
            patch("niuu.mesh.build_mesh_from_adapters_list", return_value=mock_mesh) as build,
            patch("skuld.broker.build_discovery_adapters", return_value=None),
        ):
            await b._start_mesh_adapter()

        assert build.call_args.kwargs["rpc_reply_cache_size"] == 3
        assert b._mesh_adapter is not None
        await b._mesh_adapter.stop()

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_nng_import_error_fails_closed(self, tmp_path):
        """A configured native transport must not degrade to in-process delivery."""
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={"enabled": True, "peer_id": "test-peer", "transport": "nng"},
        )
        b = Broker(settings=settings)
        b._transport = MagicMock()

        with (
            patch(
                "niuu.mesh.transport_builder.build_nng_transport",
                side_effect=ImportError("nng not available"),
            ),
            patch("skuld.broker.build_discovery_adapters", return_value=None),
        ):
            with pytest.raises(ImportError, match="nng not available"):
                await b._start_mesh_adapter()

        assert b._mesh_adapter is None

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_unbuildable_list_entry_fails_closed(self, tmp_path):
        """A configured mesh adapter that cannot be imported stops mesh startup."""
        from niuu.mesh import MeshBuildError
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={
                "enabled": True,
                "peer_id": "test-peer",
                "adapters": [{"adapter": "nonexistent.module.MeshAdapter"}],
                "discovery_adapters": [{"adapter": "static", "peers": []}],
            },
        )
        b = Broker(settings=settings)
        b._transport = MagicMock()

        with patch("skuld.broker.build_discovery_adapters") as build_discovery:
            with pytest.raises(MeshBuildError, match="nonexistent.module.MeshAdapter"):
                await b._start_mesh_adapter()

        build_discovery.assert_not_called()
        assert b._mesh_adapter is None

    @pytest.mark.asyncio
    async def test_start_mesh_adapter_mesh_start_failure_fails_closed(self, tmp_path):
        """A mesh that fails to start is raised to the broker, not logged and run."""
        from skuld.broker import Broker

        settings = SkuldSettings(
            session={"id": "s1", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            mesh={"enabled": True, "peer_id": "test-peer", "transport": "nng"},
        )
        b = Broker(settings=settings)
        b._transport = MagicMock()

        mock_mesh = MagicMock()
        mock_mesh.start = AsyncMock(side_effect=OSError("address already in use"))
        mock_discovery = MagicMock()
        mock_discovery.start = AsyncMock()
        mock_discovery.stop = AsyncMock()
        mock_discovery.watch = AsyncMock()

        with (
            patch("niuu.mesh.transport_builder.build_nng_transport", return_value=MagicMock()),
            patch(
                "niuu.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter",
                return_value=mock_mesh,
            ),
            patch("skuld.broker.build_discovery_adapters", return_value=mock_discovery),
            patch("niuu.mesh.cluster.read_cluster_pub_addresses", return_value=[]),
        ):
            with pytest.raises(OSError, match="address already in use"):
                await b._start_mesh_adapter()

        mock_discovery.stop.assert_awaited_once()
        assert b._mesh_adapter is None
