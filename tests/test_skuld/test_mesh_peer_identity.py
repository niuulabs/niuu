"""One mesh peer id per session, and a message that may have arrived is never sent twice."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.domain.peer_envelope import build_session_message
from niuu.domain.peer_message import PeerAddress, PeerKind
from niuu.forge_mcp.models import ForgeApiError
from niuu.mesh.participant import MeshParticipant
from skuld.config import MeshConfig
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.mesh_adapter import (
    MeshPeerIdError,
    SkuldMeshAdapter,
    resolve_mesh_peer_id,
)
from skuld.peer_inbox import PeerMessageUnconfirmedError
from sleipnir.adapters.in_process import InProcessBus
from tests.test_skuld.test_peer_inbox import _inbox_broker

A_ID = "aaaaaaaa-0000-4000-8000-000000000001"
B_ID = "bbbbbbbb-0000-4000-8000-000000000002"


def _mesh_config(**changes) -> MeshConfig:
    fields = {
        "enabled": True,
        "transport": "in_process",
        "rpc_timeout_s": 5.0,
        "consumes_event_types": [],
        "attestation": {"required": False},
        **changes,
    }
    return MeshConfig(**fields)


def _skuld(tmp_path, session_id: str):
    broker = _inbox_broker(tmp_path / session_id[:4], activity="idle")
    broker.session_id = session_id
    broker._settings.session.id = session_id
    broker._settings.mesh = _mesh_config()
    broker._room_bridge = None
    return broker


def _shared_bus_mesh(bus: InProcessBus):
    def build(own_peer_id, rpc_timeout_s, *, rpc_reply_cache_size, environment_id=""):
        return SleipnirMeshAdapter(
            publisher=bus,
            subscriber=bus,
            own_peer_id=own_peer_id,
            rpc_timeout_s=rpc_timeout_s,
            environment_id=environment_id,
            rpc_reply_cache_size=rpc_reply_cache_size,
        )

    return build


class TestResolvePeerId:
    def test_a_configured_id_wins(self) -> None:
        assert resolve_mesh_peer_id("mine", "session-1") == "mine"

    def test_without_one_the_session_id_is_the_peer_id_never_the_hostname(self) -> None:
        assert resolve_mesh_peer_id("", "session-1") == "session-1"

    def test_neither_is_fatal_and_names_the_remedy(self) -> None:
        with pytest.raises(MeshPeerIdError, match="Set mesh.peer_id"):
            resolve_mesh_peer_id("", None)
        with pytest.raises(MeshPeerIdError, match="Set mesh.peer_id"):
            resolve_mesh_peer_id("", "")


class TestAdapterAgreesWithItsParticipant:
    def _adapter(self, *, participant_id: str, config_id: str) -> SkuldMeshAdapter:
        return SkuldMeshAdapter(
            participant=MeshParticipant(
                mesh=SimpleNamespace(), discovery=None, peer_id=participant_id
            ),
            config=MeshConfig(enabled=True, peer_id=config_id),
            session_id="s",
            peer_inbox=SimpleNamespace(),
        )

    def test_a_participant_without_an_id_is_not_given_the_hostname(self) -> None:
        with pytest.raises(MeshPeerIdError, match="no peer id"):
            self._adapter(participant_id="", config_id="")

    def test_a_config_id_that_the_participant_does_not_run_as_is_fatal(self) -> None:
        with pytest.raises(MeshPeerIdError, match="one session has one mesh peer id"):
            self._adapter(participant_id="other", config_id="configured")

    def test_agreeing_ids_are_accepted(self) -> None:
        assert self._adapter(participant_id="p", config_id="p").peer_id == "p"
        assert self._adapter(participant_id="p", config_id="").peer_id == "p"


class TestBrokerUsesOneId:
    async def test_no_id_and_no_session_is_fatal_at_startup(self, tmp_path) -> None:
        broker = _skuld(tmp_path, A_ID)
        broker.session_id = ""

        with pytest.raises(MeshPeerIdError, match="neither mesh.peer_id nor a session id"):
            await broker._start_mesh_adapter()

        assert broker._mesh_adapter is None

    async def test_the_session_id_is_the_transport_discovery_and_certificate_id(
        self, tmp_path
    ) -> None:
        broker = _skuld(tmp_path, A_ID)
        attested: list[str] = []

        async def attest(mesh, own_peer_id):
            attested.append(own_peer_id)
            return mesh

        broker._attest_mesh = attest
        broker._settings.mesh = _mesh_config(attestation={"required": True})
        bus = InProcessBus()

        with patch("niuu.mesh.build_in_process_mesh", _shared_bus_mesh(bus)):
            await broker._start_mesh_adapter()
        try:
            assert attested == [A_ID]
            assert broker._mesh_adapter.peer_id == A_ID
            assert broker._mesh_identity.peer_id == A_ID
        finally:
            await broker._mesh_adapter.stop()

    async def test_the_certificate_request_carries_the_same_id(self, tmp_path) -> None:
        broker = _skuld(tmp_path, A_ID)
        broker._settings.forge_mcp.token = "session-token"
        seen: dict = {}

        async def attest_mesh(mesh, **kwargs):
            seen.update(kwargs)
            return mesh

        with patch("skuld.mesh_attestation.attest_mesh", attest_mesh):
            await broker._attest_mesh(SimpleNamespace(), A_ID)

        assert seen["peer_id"] == A_ID and seen["session_id"] == A_ID


class TestTwoSkuldsWithoutAConfiguredId:
    async def test_one_sends_to_the_other_by_its_announced_id_and_the_reply_arrives(
        self, tmp_path
    ) -> None:
        bus = InProcessBus()
        first, second = _skuld(tmp_path, A_ID), _skuld(tmp_path, B_ID)
        with patch("niuu.mesh.build_in_process_mesh", _shared_bus_mesh(bus)):
            await first._start_mesh_adapter()
            await second._start_mesh_adapter()
        try:
            assert first._mesh_adapter.peer_id == A_ID
            assert second._mesh_adapter.peer_id == B_ID
            sender = BrokerForgeMcpHost(first, client=SimpleNamespace())
            recipient = BrokerForgeMcpHost(second, client=SimpleNamespace())

            sent = await sender.send_mesh_message(
                B_ID, "are you there?", expect_reply=True, request_id="r-1", in_reply_to=""
            )

            assert sent["status"] == "accepted" and sent["path"] == "mesh"
            [(message_id, entry)] = second._peer_inbox.items()
            assert entry.envelope.content == "are you there?"
            assert entry.envelope.reply.target == A_ID
            assert message_id == sent["message_id"]

            replied = await recipient.send_mesh_message(
                entry.envelope.reply.target,
                "yes",
                expect_reply=False,
                request_id="r-2",
                in_reply_to=message_id,
            )

            assert replied["status"] == "accepted"
            [answer] = first._peer_inbox.values()
            assert answer.envelope.content == "yes"
            assert answer.envelope.in_reply_to == message_id
        finally:
            await first._mesh_adapter.stop()
            await second._mesh_adapter.stop()


class TestAConfirmTimeoutThenARetry:
    """The recipient's Forge did not confirm in time; the same request_id lands once."""

    def _recipient(self, tmp_path, receipts):
        broker = _inbox_broker(tmp_path, activity="running")
        broker._await_event_log_receipt = AsyncMock(side_effect=receipts)
        return broker

    def _envelope(self, request_id: str):
        return build_session_message(
            sender=PeerAddress(PeerKind.SESSION, "sender-session", name="planner"),
            recipient=PeerAddress(PeerKind.SESSION, _inbox_broker.__globals__["SESSION_ID"]),
            content="Please review",
            request_id=request_id,
        )

    async def test_the_retry_is_the_same_message_and_is_delivered_once(self, tmp_path) -> None:
        broker = self._recipient(tmp_path, [None, True, True])
        first = self._envelope("req-1")

        with pytest.raises(PeerMessageUnconfirmedError, match="may have been stored"):
            await broker.accept_peer_message(first, "req-1")
        assert broker._peer_inbox == {}

        retry = self._envelope("req-1")
        assert retry.message_id == first.message_id
        accepted = await broker.accept_peer_message(retry, "req-1")
        again = await broker.accept_peer_message(self._envelope("req-1"), "req-1")

        assert accepted.duplicate is False and again.duplicate is True
        assert list(broker._peer_inbox) == [first.message_id]

    async def test_a_new_request_id_is_a_new_message(self, tmp_path) -> None:
        broker = self._recipient(tmp_path, [True, True])

        await broker.accept_peer_message(self._envelope("req-1"), "req-1")
        await broker.accept_peer_message(self._envelope("req-2"), "req-2")

        assert len(broker._peer_inbox) == 2

    async def test_forge_is_told_pending_and_not_failed(self, tmp_path) -> None:
        broker = self._recipient(tmp_path, [None])
        broker._emit_delivery_ack = AsyncMock()
        envelope = self._envelope("req-1")

        await broker._accept_peer_message_frame(
            {"type": "peer_message", "envelope": envelope.to_wire(), "request_id": "req-1"}
        )

        [call] = broker._emit_delivery_ack.await_args_list
        assert call.args[:3] == ("req-1", envelope.message_id, "pending")
        assert "same request_id" in call.kwargs["error"]

    async def test_another_refusal_is_still_failed(self, tmp_path) -> None:
        broker = self._recipient(tmp_path, [])
        broker._settings.peer_inbox.enabled = False
        broker._emit_delivery_ack = AsyncMock()
        envelope = self._envelope("req-1")

        await broker._accept_peer_message_frame(
            {"type": "peer_message", "envelope": envelope.to_wire(), "request_id": "req-1"}
        )

        assert broker._emit_delivery_ack.await_args.args[2] == "failed"

    async def test_the_mesh_answer_is_unconfirmed_not_refused(self, tmp_path) -> None:
        inbox = SimpleNamespace(
            accept_peer_message=AsyncMock(side_effect=PeerMessageUnconfirmedError("not stored yet"))
        )
        adapter = SkuldMeshAdapter(
            participant=MeshParticipant(mesh=SimpleNamespace(), discovery=None, peer_id="p"),
            config=MeshConfig(enabled=True, peer_id="p"),
            session_id="s",
            peer_inbox=inbox,
        )
        envelope = self._envelope("req-1")

        answer = await adapter._accept(envelope)

        assert answer["status"] == "unconfirmed" and answer["message_id"] == envelope.message_id

    async def test_the_sender_gets_a_maybe_delivered_error_with_the_retry_hint(
        self, tmp_path
    ) -> None:
        broker = _skuld(tmp_path, A_ID)
        broker._mesh_adapter = SimpleNamespace(
            peer_id=A_ID,
            send_peer_message=AsyncMock(
                return_value={"status": "unconfirmed", "error": "not stored yet"}
            ),
        )
        host = BrokerForgeMcpHost(broker, client=SimpleNamespace())

        with pytest.raises(ForgeApiError, match="did not confirm the message") as raised:
            await host.send_mesh_message(
                B_ID, "hi", expect_reply=True, request_id="r-1", in_reply_to=""
            )

        assert raised.value.status == 504

    async def test_from_the_tool_to_one_delivery_end_to_end(self, tmp_path) -> None:
        """A model that retries with the request_id the tool gave it delivers exactly once."""
        from niuu.forge_mcp.models import ForgeMcpGrant
        from niuu.forge_mcp.tools import ForgeMcpToolbox
        from tests.test_niuu.test_forge_mcp_tools import LIMITS, FakeClient, FakeHost

        recipient = self._recipient(tmp_path, [None, True, True])
        sender_id = "sender-session"

        class Forge(FakeClient):
            """Forge as the sender sees it: relays the envelope; a confirm timeout is 202."""

            async def send_message(self, session_id, *, content, request_id, **kwargs):
                self.calls.append(("send_message", (session_id, content, request_id, None)))
                envelope = build_session_message(
                    sender=PeerAddress(PeerKind.SESSION, sender_id),
                    recipient=PeerAddress(PeerKind.SESSION, session_id),
                    content=content,
                    request_id=request_id,
                )
                try:
                    await recipient.accept_peer_message(envelope, request_id)
                except PeerMessageUnconfirmedError:
                    return {"status": "pending", "delivery": "pending", "request_id": request_id}
                return {"status": "delivered", "delivery": "delivered", "request_id": request_id}

        client = Forge()
        host = FakeHost()
        toolbox = ForgeMcpToolbox(
            client=client,
            host=host,
            grants=[ForgeMcpGrant.MESSAGE],
            limits=LIMITS,
        )
        target = _inbox_broker.__globals__["SESSION_ID"]

        first = await toolbox.call("send_message", {"session_id": target, "content": "review"})
        assert first.structured["status"] == "pending"
        assert recipient._peer_inbox == {}
        request_id = first.structured["request_id"]
        assert request_id in first.structured["note"]

        second = await toolbox.call(
            "send_message", {"session_id": target, "content": "review", "request_id": request_id}
        )
        third = await toolbox.call(
            "send_message", {"session_id": target, "content": "review", "request_id": request_id}
        )

        assert second.structured["status"] == "delivered"
        assert third.structured["status"] == "delivered"
        assert len(recipient._peer_inbox) == 1
        assert [m.envelope.content for m in recipient._peer_inbox.values()] == ["review"]
