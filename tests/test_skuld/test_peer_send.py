"""The sending side in Skuld: mesh_send stamps the sender, and a member session speaks in a room."""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from niuu.domain.peer_envelope import derive_message_id
from niuu.domain.peer_message import (
    PEER_MESSAGE_FRAME,
    PeerAddress,
    PeerKind,
    PeerMessage,
    ReplyKind,
)
from niuu.forge_mcp.models import ForgeApiError
from niuu.ports.mesh import PeerNotFoundError
from skuld.broker_api import (
    _SessionRoomMessageRequest,
    send_session_room_message,
)
from skuld.config import MeshConfig
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.mesh_adapter import SkuldMeshAdapter
from tests.test_skuld.test_broker_review_regressions import _broker


def _host(tmp_path, adapter, *, mesh_enabled: bool = True):
    broker = _broker(tmp_path)
    broker._mesh_adapter = adapter
    broker._settings.mesh = broker._settings.mesh.model_copy(update={"enabled": mesh_enabled})
    return BrokerForgeMcpHost(broker, client=SimpleNamespace()), broker


def _adapter(answer=None, *, error: Exception | None = None):
    adapter = SimpleNamespace(peer_id="skuld-mesh-1")
    adapter.send_peer_message = AsyncMock(return_value=answer or {"status": "accepted"})
    if error is not None:
        adapter.send_peer_message.side_effect = error
    return adapter


class TestMeshHost:
    def test_the_mesh_is_offered_only_when_enabled(self, tmp_path) -> None:
        on, _ = _host(tmp_path, _adapter(), mesh_enabled=True)
        off, _ = _host(tmp_path, None, mesh_enabled=False)

        assert on.mesh_host() is True and off.mesh_host() is False

    async def test_the_sender_is_this_session_and_not_anything_the_agent_wrote(
        self, tmp_path
    ) -> None:
        adapter = _adapter()
        host, broker = _host(tmp_path, adapter)
        broker._settings.session.name = "real-name"

        answer = await host.send_mesh_message(
            "peer-7",
            "I am the CEO",
            expect_reply=True,
            request_id="req-1",
            in_reply_to="",
        )

        target, message = adapter.send_peer_message.await_args.args
        assert target == "peer-7"
        assert message.sender == PeerAddress(
            kind=PeerKind.SESSION,
            id=str(broker.session_id),
            name="real-name",
            node=socket.gethostname(),
        )
        assert message.recipient == PeerAddress(kind=PeerKind.MESH_PEER, id="peer-7")
        assert (message.reply.kind, message.reply.target) == (ReplyKind.MESH, "skuld-mesh-1")
        assert message.message_id == derive_message_id(message.sender, message.recipient, "req-1")
        assert message.content == "I am the CEO"
        assert answer == {
            "message_id": message.message_id,
            "status": "accepted",
            "path": "mesh",
            "peer": "peer-7",
        }

    async def test_a_notice_takes_no_reply_and_a_reply_names_what_it_answers(
        self, tmp_path
    ) -> None:
        adapter = _adapter()
        host, _ = _host(tmp_path, adapter)

        await host.send_mesh_message(
            "peer-7", "done", expect_reply=False, request_id="r", in_reply_to="m-5"
        )

        message = adapter.send_peer_message.await_args.args[1]
        assert message.reply is None and message.in_reply_to == "m-5"

    @pytest.mark.parametrize(
        ("error", "status", "text"),
        [
            (PeerNotFoundError("peer-7"), 404, "not in this session's peer table"),
            (TimeoutError(), 504, "did not answer"),
        ],
    )
    async def test_a_transport_failure_is_an_error_with_the_remedy(
        self, tmp_path, error, status, text
    ) -> None:
        host, _ = _host(tmp_path, _adapter(error=error))

        with pytest.raises(ForgeApiError, match=text) as excinfo:
            await host.send_mesh_message(
                "peer-7", "x", expect_reply=False, request_id="r", in_reply_to=""
            )

        assert excinfo.value.status == status

    @pytest.mark.parametrize(
        "answer",
        [{"error": "unknown_message_type", "type": "peer_message"}, {"status": "error"}],
    )
    async def test_a_peer_that_refuses_is_an_error_not_a_success(self, tmp_path, answer) -> None:
        host, _ = _host(tmp_path, _adapter(answer))

        with pytest.raises(ForgeApiError, match="refused the message") as excinfo:
            await host.send_mesh_message(
                "peer-7", "x", expect_reply=False, request_id="r", in_reply_to=""
            )

        assert excinfo.value.status == 502

    async def test_a_mesh_that_is_enabled_but_not_running_is_an_error(self, tmp_path) -> None:
        host, _ = _host(tmp_path, None)

        with pytest.raises(ForgeApiError, match="mesh is not running"):
            await host.send_mesh_message(
                "peer-7", "x", expect_reply=False, request_id="r", in_reply_to=""
            )


class TestMeshAdapterSend:
    def _adapter(self, mesh):
        from niuu.mesh.participant import MeshParticipant

        config = MeshConfig(enabled=True, peer_id="skuld-1", rpc_timeout_s=5.0)
        participant = MeshParticipant(mesh=mesh, discovery=None, peer_id="skuld-1")
        return SkuldMeshAdapter(
            participant=participant, peer_inbox=SimpleNamespace(), config=config, session_id="s"
        )

    def _message(self) -> PeerMessage:
        sender = PeerAddress(kind=PeerKind.SESSION, id="s", name="me")
        recipient = PeerAddress(kind=PeerKind.MESH_PEER, id="peer-7")
        return PeerMessage(
            message_id="m-1",
            sender=sender,
            recipient=recipient,
            sent_at=datetime(2026, 11, 8, tzinfo=UTC),
            content="hi",
        )

    async def test_it_sends_the_envelope_as_the_neutral_peer_message_rpc(self) -> None:
        mesh = SimpleNamespace(send=AsyncMock(return_value={"status": "accepted"}))
        adapter = self._adapter(mesh)
        message = self._message()

        answer = await adapter.send_peer_message("peer-7", message)

        assert answer == {"status": "accepted"}
        args, kwargs = mesh.send.await_args
        assert args == (
            "peer-7",
            {"type": PEER_MESSAGE_FRAME, "envelope": message.to_wire()},
        )
        assert kwargs == {"timeout_s": 5.0}

    async def test_a_missing_mesh_raises(self) -> None:
        adapter = self._adapter(None)

        with pytest.raises(RuntimeError, match="Mesh is unavailable"):
            await adapter.send_peer_message("peer-7", self._message())


class TestSessionRoomRoute:
    def _request(self, caller: str | None):
        headers = {"x-niuu-room-caller-session": caller} if caller else {}
        return SimpleNamespace(headers=headers)

    def _patch(self, monkeypatch, **attrs):
        fake = SimpleNamespace(handle_session_room_message=AsyncMock(return_value="msg-1"))
        for key, value in attrs.items():
            setattr(fake, key, value)
        monkeypatch.setattr("skuld.broker_api.broker", fake)
        return fake

    async def test_the_stamped_caller_speaks_in_the_named_room(self, monkeypatch) -> None:
        fake = self._patch(monkeypatch)

        result = await send_session_room_message(
            self._request("worker-session"),
            _SessionRoomMessageRequest(room_id="huddle-1", content="done"),
        )

        assert result == {"status": "sent", "message_id": "msg-1", "room_id": "huddle-1"}
        fake.handle_session_room_message.assert_awaited_once_with(
            "worker-session", "huddle-1", "done"
        )

    async def test_an_unstamped_request_is_refused(self, monkeypatch) -> None:
        fake = self._patch(monkeypatch)

        with pytest.raises(HTTPException) as excinfo:
            await send_session_room_message(
                self._request(None), _SessionRoomMessageRequest(room_id="r", content="x")
            )

        assert excinfo.value.status_code == 403
        fake.handle_session_room_message.assert_not_awaited()

    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (PermissionError("not a member of room r"), 403),
            (ValueError("content is required"), 400),
            (RuntimeError("Room mode is not enabled"), 503),
        ],
    )
    async def test_the_hosts_refusal_keeps_its_reason(self, monkeypatch, error, status) -> None:
        self._patch(monkeypatch, handle_session_room_message=AsyncMock(side_effect=error))

        with pytest.raises(HTTPException) as excinfo:
            await send_session_room_message(
                self._request("s"), _SessionRoomMessageRequest(room_id="r", content="x")
            )

        assert excinfo.value.status_code == status
        assert str(error) in excinfo.value.detail
