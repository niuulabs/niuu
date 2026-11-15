"""Direct routing and ``ask`` over the mesh: the recipient's Skuld acks and reports its state."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.domain.peer_message import PeerKind
from niuu.forge_mcp.models import ForgeApiError
from niuu.mesh.identity import MeshPeer
from niuu.ports.mesh import PeerNotFoundError
from skuld.config import SkuldSettings
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.mesh_adapter import SkuldMeshAdapter
from skuld.peer_inbox import PEER_STATE_RPC_TYPE
from skuld.transports.mcp_config import build_claude_mcp_payload, normalize_mcp_servers
from tests.test_skuld.test_ask import _answer as _session_answer
from tests.test_skuld.test_peer_inbox import SESSION_ID, _inbox_broker, _message, _settle

PEER = "peer-9"
QUESTION = "mesh-question-1"


def _peer(peer_id: str, name: str = "") -> MeshPeer:
    return MeshPeer(
        peer_id=peer_id,
        realm_id="realm",
        persona="coder",
        capabilities=[],
        permission_mode="full_access",
        version="1",
        display_name=name,
        participant_type="skuld",
    )


class FakeAdapter:
    """What the host needs of the mesh adapter: the peer table, the send and the state RPC."""

    def __init__(self, *peers: MeshPeer) -> None:
        self.peer_id = "my-peer"
        self.table = {peer.peer_id: peer for peer in peers}
        self.send_peer_message = AsyncMock(return_value={"status": "accepted"})
        self.request_peer_state = AsyncMock(
            return_value={"status": "ok", "activity_state": "active", "waiting": []}
        )

    def peers(self):
        return dict(self.table)


def _host(tmp_path, adapter, *, enabled: bool = True, **ask):
    broker = _inbox_broker(tmp_path, activity="active")
    broker._mesh_adapter = adapter
    broker._settings.mesh = broker._settings.mesh.model_copy(update={"enabled": enabled})
    broker._settings.ask = broker._settings.ask.model_copy(
        update={"poll_interval_s": 0.01, "wait_max_s": 5.0, **ask}
    )
    return BrokerForgeMcpHost(broker, client=SimpleNamespace()), broker


async def _ask(host, *, deadline_s=0.2, confirmed=True, message_id=QUESTION):
    return await host.ask_for_answer(
        recipient_id=PEER,
        instance_id=None,
        message_id=message_id,
        request_id="req-1",
        delivery_confirmed=confirmed,
        deadline_s=deadline_s,
        mesh_peer=PEER,
    )


def _reply(message_id: str = "answer-1"):
    return _message(
        message_id,
        sender_id=PEER,
        recipient_id=SESSION_ID,
        in_reply_to=QUESTION,
        content="Yes, ship it.",
    )


class TestPeerLookup:
    async def test_a_session_id_in_the_peer_table_is_a_peer(self, tmp_path) -> None:
        host, _ = _host(tmp_path, FakeAdapter(_peer("sess-1"), _peer("sess-2")))

        assert await host.mesh_peer_for(session_id="sess-1", name=None) == "sess-1"
        assert await host.mesh_peer_for(session_id="sess-3", name=None) is None

    async def test_a_name_matches_the_peer_id_or_the_announced_session_name(self, tmp_path) -> None:
        host, _ = _host(
            tmp_path, FakeAdapter(_peer("sess-1", "builder"), _peer("sess-2", "tester"))
        )

        assert await host.mesh_peer_for(session_id=None, name="sess-2") == "sess-2"
        assert await host.mesh_peer_for(session_id=None, name="builder") == "sess-1"
        assert await host.mesh_peer_for(session_id=None, name="nobody") is None

    async def test_a_name_two_peers_carry_is_an_error_that_lists_them(self, tmp_path) -> None:
        host, _ = _host(tmp_path, FakeAdapter(_peer("a", "twin"), _peer("b", "twin")))

        with pytest.raises(ForgeApiError, match="2 mesh peers are named 'twin': a, b"):
            await host.mesh_peer_for(session_id=None, name="twin")

    async def test_without_a_mesh_nobody_is_a_peer(self, tmp_path) -> None:
        host, _ = _host(tmp_path, None, enabled=False)

        assert await host.mesh_peer_for(session_id="sess-1", name=None) is None

    async def test_a_mesh_that_should_run_but_does_not_raises_and_never_means_forge(
        self, tmp_path
    ) -> None:
        host, _ = _host(tmp_path, None, enabled=True)

        with pytest.raises(ForgeApiError, match="not rerouted through Forge") as raised:
            await host.mesh_peer_for(session_id="sess-1", name=None)
        assert raised.value.status == 503


class TestAdapterTable:
    def test_the_adapter_reads_the_discovery_table(self) -> None:
        discovery = SimpleNamespace(peers=lambda: {"p": _peer("p")})
        participant = SimpleNamespace(mesh=None, discovery=discovery, peer_id="me")
        adapter = SkuldMeshAdapter(participant, SkuldSettings().mesh, "sess", SimpleNamespace())

        assert list(adapter.peers()) == ["p"]

    def test_no_discovery_means_an_empty_table(self) -> None:
        participant = SimpleNamespace(mesh=None, discovery=None, peer_id="me")
        adapter = SkuldMeshAdapter(participant, SkuldSettings().mesh, "sess", SimpleNamespace())

        assert adapter.peers() == {}

    async def test_the_state_rpc_goes_to_the_peer(self) -> None:
        mesh = SimpleNamespace(send=AsyncMock(return_value={"status": "ok"}))
        participant = SimpleNamespace(mesh=mesh, discovery=None, peer_id="me")
        adapter = SkuldMeshAdapter(participant, SkuldSettings().mesh, "sess", SimpleNamespace())

        await adapter.request_peer_state("p")

        assert mesh.send.await_args.args == ("p", {"type": PEER_STATE_RPC_TYPE})

    async def test_the_state_rpc_with_no_mesh_raises(self) -> None:
        participant = SimpleNamespace(mesh=None, discovery=None, peer_id="me")
        adapter = SkuldMeshAdapter(participant, SkuldSettings().mesh, "sess", SimpleNamespace())

        with pytest.raises(RuntimeError, match="Mesh is unavailable"):
            await adapter.request_peer_state("p")


class TestSendEnvelope:
    async def test_an_urgent_mesh_message_is_stamped_urgent_to_the_peer(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        host, broker = _host(tmp_path, adapter)

        answer = await host.send_mesh_message(
            PEER, "now", expect_reply=True, request_id="r", in_reply_to="", urgent=True
        )

        message = adapter.send_peer_message.await_args.args[1]
        assert message.urgent is True and message.recipient.kind is PeerKind.MESH_PEER
        assert message.sender.id == str(broker.session_id)
        assert answer["path"] == "mesh" and answer["status"] == "accepted"

    async def test_a_peer_that_refuses_gives_the_reason(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        adapter.send_peer_message.return_value = {"status": "refused", "error": "inbox is off"}
        host, _ = _host(tmp_path, adapter)

        with pytest.raises(ForgeApiError, match="inbox is off"):
            await host.send_mesh_message(
                PEER, "x", expect_reply=True, request_id="r", in_reply_to=""
            )


class TestAskOverTheMesh:
    async def test_the_answer_is_matched_by_in_reply_to_in_the_own_inbox(self, tmp_path) -> None:
        host, broker = _host(tmp_path, FakeAdapter(_peer(PEER)))
        asking = asyncio.create_task(_ask(host, deadline_s=30))
        await asyncio.sleep(0.05)

        await broker.accept_peer_message(_reply())
        result = await asyncio.wait_for(asking, 2)
        await _settle()

        assert result["outcome"] == "answered" and result["answer"] == "Yes, ship it."
        assert broker._transport.send_message.await_count == 0

    async def test_the_peers_own_state_is_read_over_the_mesh_not_from_forge(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        host, _ = _host(tmp_path, adapter)

        await _ask(host, deadline_s=0.05)

        assert adapter.request_peer_state.await_count >= 1
        assert adapter.request_peer_state.await_args.args == (PEER,)

    async def test_a_peer_that_dropped_from_the_table_is_gone(self, tmp_path) -> None:
        adapter = FakeAdapter()  # the peer is not in the table any more
        host, _ = _host(tmp_path, adapter)

        result = await _ask(host)

        assert result["outcome"] == "gone" and "peer table" in result["detail"]
        assert adapter.request_peer_state.await_count == 0

    async def test_a_peer_that_vanishes_while_its_state_is_read_is_gone(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        adapter.request_peer_state.side_effect = PeerNotFoundError(PEER)
        host, _ = _host(tmp_path, adapter)

        assert (await _ask(host))["outcome"] == "gone"

    async def test_a_peer_whose_transport_stopped_is_gone(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        adapter.request_peer_state.return_value = {"status": "ok", "activity_state": "stopped"}
        host, _ = _host(tmp_path, adapter)

        assert (await _ask(host))["outcome"] == "gone"

    async def test_a_peer_blocked_on_a_person_is_blocked(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        adapter.request_peer_state.return_value = {
            "status": "ok",
            "activity_state": "awaiting_input",
            "kind": "permission",
        }
        host, _ = _host(tmp_path, adapter)

        result = await _ask(host)

        assert result["outcome"] == "blocked" and "permission" in result["detail"]

    async def test_a_message_the_peer_has_not_read_is_not_started(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        adapter.request_peer_state.return_value = {
            "status": "ok",
            "activity_state": "active",
            "waiting": [QUESTION],
        }
        host, _ = _host(tmp_path, adapter)

        assert (await _ask(host, deadline_s=0.05))["outcome"] == "not_started"

    async def test_an_unconfirmed_send_is_delivery_unknown_naming_the_mesh_knob(
        self, tmp_path
    ) -> None:
        host, _ = _host(tmp_path, FakeAdapter(_peer(PEER)))

        result = await _ask(host, deadline_s=0.05, confirmed=False)

        assert result["outcome"] == "delivery_unknown"
        assert (
            "mesh.rpc_timeout_s" in result["detail"] and "unknown, not failed" in result["detail"]
        )

    async def test_a_state_rpc_that_times_out_is_named_in_the_deadline(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        adapter.request_peer_state.side_effect = TimeoutError()
        host, _ = _host(tmp_path, adapter)

        result = await _ask(host, deadline_s=0.05)

        assert result["outcome"] == "deadline"
        assert "state could not be read" in result["detail"]

    async def test_a_peer_without_the_state_rpc_is_named_in_the_deadline(self, tmp_path) -> None:
        adapter = FakeAdapter(_peer(PEER))
        adapter.request_peer_state.return_value = {"error": "unknown_message_type"}
        host, _ = _host(tmp_path, adapter)

        result = await _ask(host, deadline_s=0.05)

        assert result["outcome"] == "deadline" and "unknown_message_type" in result["detail"]

    async def test_a_mesh_that_stopped_during_the_wait_raises(self, tmp_path) -> None:
        host, broker = _host(tmp_path, FakeAdapter(_peer(PEER)))
        broker._mesh_adapter = None

        with pytest.raises(ForgeApiError, match="mesh is not running"):
            await _ask(host)


class TestPeerStateRpc:
    async def test_it_reports_what_the_session_is_doing_and_has_not_read(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, activity="active")
        await broker.accept_peer_message(_session_answer("not-read"))

        answer = await broker.peer_state_rpc({"type": PEER_STATE_RPC_TYPE})

        assert answer == {"status": "ok", "activity_state": "active", "waiting": ["not-read"]}

    async def test_a_blocked_session_says_on_what(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, activity="awaiting_input")
        broker._activity_extra = {"kind": "question"}

        answer = await broker.peer_state_rpc({})

        assert answer["activity_state"] == "awaiting_input" and answer["kind"] == "question"


class TestClaudeToolTimeout:
    def test_a_servers_tool_timeout_becomes_claudes_per_server_timeout_in_ms(self) -> None:
        servers = normalize_mcp_servers(
            [
                {"name": "forge", "command": "x", "tool_timeout_sec": 120.0},
                {"name": "remote", "url": "https://x.test/mcp", "tool_timeout_sec": 7.5},
                {"name": "plain", "command": "y"},
            ]
        )

        entries = (build_claude_mcp_payload(servers) or {})["mcpServers"]

        assert entries["forge"]["timeout"] == 120000
        assert entries["remote"]["timeout"] == 7500
        assert "timeout" not in entries["plain"]

    def test_the_built_in_forge_server_carries_forge_mcp_tool_timeout_s(self, tmp_path) -> None:
        broker = _inbox_broker(tmp_path, forge_mcp={"enabled": True, "tool_timeout_s": 90.0})
        servers = normalize_mcp_servers(broker._effective_mcp_servers())

        forge = (build_claude_mcp_payload(servers) or {})["mcpServers"][
            broker._settings.forge_mcp.server_name
        ]
        assert forge["timeout"] == 90000
