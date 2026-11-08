# ruff: noqa: F811  (the `flock` fixture is shared from test_mesh_threads)
"""room_post names a room, not its host: the session knows which rooms it is in, and posts
straight to the host over the mesh when it can, through Forge when it cannot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.domain.peer_message import PeerAddress, PeerKind, PeerMessage, ReplyKind, ReplyRoute
from niuu.forge_mcp.models import ForgeApiError
from niuu.mesh.attestation import VERIFIED_SENDER_KEY
from niuu.ports.mesh import PeerNotFoundError
from tests.test_skuld.test_mesh_threads import Node, _certs, flock  # noqa: F401

CERTS = _certs("member", "outsider")


@dataclass
class _Seat:
    """What the room bridge returns for a participant that joined."""

    room_ids: tuple[str, ...]


async def _told_of_room(member: Node, room_id: str, host: str, *, node: str = "n1") -> None:
    """The host speaks in the room to the member: that is how the member learns where it is."""
    await member.broker.accept_peer_message(
        PeerMessage(
            message_id=f"{room_id}-{host}-{len(member.broker._peer_inbox)}",
            sender=PeerAddress(PeerKind.SESSION, host, node=node),
            recipient=PeerAddress(PeerKind.SESSION, member.broker.session_id),
            sent_at=datetime(2026, 11, 8, tzinfo=UTC),
            content=f"You were seated in room {room_id}, hosted by session {host}.",
            reply=ReplyRoute(ReplyKind.ROOM, host, node=node),
            room_id=room_id,
            relayed_by=PeerAddress(PeerKind.SESSION, host, node=node),
        )
    )


class TestTheSessionKnowsItsRooms:
    async def test_a_room_and_its_host_are_learned_from_what_is_said_in_it(self, flock) -> None:
        member = await flock.add("member", certs=CERTS)

        await _told_of_room(member, "huddle-1", "host-a")
        await _told_of_room(member, "huddle-2", "host-b", node="n2")

        assert member.broker.room_seats() == {
            "huddle-1": {"host-a": "n1"},
            "huddle-2": {"host-b": "n2"},
        }

    async def test_a_message_from_no_room_adds_none(self, flock) -> None:
        member = await flock.add("member", certs=CERTS)
        await member.broker.accept_peer_message(
            PeerMessage(
                message_id="m",
                sender=PeerAddress(PeerKind.SESSION, "someone"),
                recipient=PeerAddress(PeerKind.SESSION, member.broker.session_id),
                sent_at=datetime(2026, 11, 8, tzinfo=UTC),
                content="hello",
            )
        )

        assert member.broker.room_seats() == {}

    async def test_a_room_it_was_never_told_of_is_refused_listing_the_rooms_it_is_in(
        self, flock
    ) -> None:
        member = await flock.add("member", certs=CERTS)
        await _told_of_room(member, "huddle-1", "host-a")

        with pytest.raises(ForgeApiError, match="not in room 'other'.*huddle-1") as excinfo:
            await member.host.post_room_message("other", "hi")

        assert excinfo.value.status == 403

    async def test_two_hosts_using_one_room_id_need_the_host_named(self, flock) -> None:
        member = await flock.add("member", certs=CERTS)
        await _told_of_room(member, "huddle", "host-a")
        await _told_of_room(member, "huddle", "host-b")
        member.host._client = SimpleNamespace(
            post_room_message=AsyncMock(return_value={"message_id": "m1"})
        )

        with pytest.raises(ForgeApiError, match="pass host_session_id") as excinfo:
            await member.host.post_room_message("huddle", "hi")
        posted = await member.host.post_room_message("huddle", "hi", host_session_id="host-b")

        assert excinfo.value.status == 409 and posted["host_session_id"] == "host-b"


class TestPostingOverTheMesh:
    async def _world(self, flock, *, signed=True):
        host = await flock.add(
            "host", session_id="host", certs=_certs("member", "host"), signed=signed
        )
        member = await flock.add("member", certs=_certs("member", "host"), signed=signed)
        host.broker.handle_session_room_message = AsyncMock(return_value="room-msg-9")
        await _told_of_room(member, "huddle-1", "host")
        member.host._client = SimpleNamespace(
            post_room_message=AsyncMock(return_value={"message_id": "via-forge"})
        )
        return host, member

    async def test_a_host_that_is_a_mesh_peer_is_posted_to_directly_and_takes_the_verified_caller(
        self, flock
    ) -> None:
        host, member = await self._world(flock)

        posted = await member.host.post_room_message("huddle-1", "hi all")

        assert posted == {
            "room_id": "huddle-1",
            "host_session_id": "host",
            "path": "mesh",
            "status": "accepted",
            "message_id": "room-msg-9",
        }
        # The host acted for the session the mesh certified, not one the message named.
        host.broker.handle_session_room_message.assert_awaited_once_with(
            "session-member", "huddle-1", "hi all"
        )
        member.host._client.post_room_message.assert_not_awaited()

    async def test_a_host_that_refuses_the_post_gives_its_reason(self, flock) -> None:
        host, member = await self._world(flock)
        host.broker.handle_session_room_message.side_effect = PermissionError(
            "Session session-member is not a member of room huddle-1 on this session"
        )

        with pytest.raises(ForgeApiError, match="did not take the post.*not a member") as excinfo:
            await member.host.post_room_message("huddle-1", "hi")

        assert excinfo.value.status == 403

    async def test_a_host_that_is_not_a_mesh_peer_is_reached_through_forge(self, flock) -> None:
        member = await flock.add("member", certs=CERTS)
        await _told_of_room(member, "huddle-1", "forge-only-host")
        member.host._client = SimpleNamespace(
            post_room_message=AsyncMock(return_value={"message_id": "via-forge"})
        )

        posted = await member.host.post_room_message("huddle-1", "hi")

        assert posted["path"] == "forge" and posted["message_id"] == "via-forge"
        member.host._client.post_room_message.assert_awaited_once_with(
            "forge-only-host", room_id="huddle-1", content="hi"
        )

    async def test_with_mesh_signing_off_the_post_goes_through_forge_which_stamps_the_caller(
        self, flock
    ) -> None:
        host, member = await self._world(flock, signed=False)

        posted = await member.host.post_room_message("huddle-1", "hi")

        assert posted["path"] == "forge"
        host.broker.handle_session_room_message.assert_not_awaited()

    @pytest.mark.parametrize(
        ("error", "status", "text"),
        [
            (PeerNotFoundError("host"), 404, "left this session's peer table"),
            (TimeoutError(), 504, "did not answer within mesh.rpc_timeout_s"),
        ],
    )
    async def test_a_transport_failure_is_an_error_not_a_reroute_through_forge(
        self, flock, error, status, text
    ) -> None:
        host, member = await self._world(flock)
        member.adapter.send_rpc = AsyncMock(side_effect=error)  # type: ignore[method-assign]

        with pytest.raises(ForgeApiError, match=text) as excinfo:
            await member.host.post_room_message("huddle-1", "hi")

        assert excinfo.value.status == status
        member.host._client.post_room_message.assert_not_awaited()


class TestTheHostsRpc:
    async def test_it_refuses_when_the_mesh_does_not_verify_senders(self, flock) -> None:
        host = await flock.add("host", signed=False)
        host.broker.handle_session_room_message = AsyncMock()

        answer = await host.broker.handle_room_message_rpc(
            {
                "type": "room_message",
                "room_id": "r",
                "content": "x",
                VERIFIED_SENDER_KEY: {"session_id": "s"},
            }
        )

        assert answer["status"] == "refused" and "post through Forge" in answer["error"]
        host.broker.handle_session_room_message.assert_not_awaited()

    async def test_it_refuses_a_caller_the_mesh_did_not_verify(self, flock) -> None:
        host = await flock.add("host")
        host.broker.handle_session_room_message = AsyncMock()

        answer = await host.broker.handle_room_message_rpc(
            {"type": "room_message", "room_id": "r", "content": "x", "session_id": "forged"}
        )

        assert answer["status"] == "refused" and "verified session" in answer["error"]
        host.broker.handle_session_room_message.assert_not_awaited()

    async def test_it_takes_the_caller_from_the_verified_sender_over_any_claim(self, flock) -> None:
        host = await flock.add("host")
        host.broker.handle_session_room_message = AsyncMock(return_value="m-1")

        answer = await host.broker.handle_room_message_rpc(
            {
                "type": "room_message",
                "room_id": "r",
                "content": "x",
                "caller_session_id": "forged",
                VERIFIED_SENDER_KEY: {"peer_id": "p", "session_id": "real-session"},
            }
        )

        assert answer == {"status": "accepted", "message_id": "m-1"}
        host.broker.handle_session_room_message.assert_awaited_once_with("real-session", "r", "x")

    @pytest.mark.parametrize("body", [{"room_id": 3, "content": "x"}, {"room_id": "r"}])
    async def test_it_refuses_a_malformed_post(self, flock, body) -> None:
        host = await flock.add("host")

        answer = await host.broker.handle_room_message_rpc(
            {"type": "room_message", **body, VERIFIED_SENDER_KEY: {"session_id": "s"}}
        )

        assert answer["status"] == "refused" and "room_id and content" in answer["error"]

    async def test_a_room_without_room_mode_is_an_error_not_a_refusal(self, flock) -> None:
        host = await flock.add("host")  # no room bridge on this broker

        answer = await host.broker.handle_room_message_rpc(
            {
                "type": "room_message",
                "room_id": "r",
                "content": "x",
                VERIFIED_SENDER_KEY: {"session_id": "s"},
            }
        )

        assert answer["status"] == "error" and "Room mode is not enabled" in answer["error"]


class TestSeatNotice:
    async def test_a_session_that_joins_is_told_which_room_and_who_hosts_it(self, flock) -> None:
        host = await flock.add("host", session_id="host")
        told: list[tuple[str, str, str]] = []

        async def tell_member(peer_id, content, room_id):
            told.append((peer_id, content, room_id))

        host.broker._room_bridge = SimpleNamespace(
            join_environment=AsyncMock(return_value=_Seat(room_ids=("r1", "r2"))),
            tell_member=tell_member,
        )
        host.broker._emit_broker_frame = AsyncMock()

        await host.broker.join_environment(
            participant_id="agent-1", display_name="Agent", environment_id="e", session_id="s-9"
        )

        assert [(p, r) for p, _, r in told] == [("agent-1", "r1"), ("agent-1", "r2")]
        assert 'hosted by session host. Speak in it with room_post(room_id="r1"' in told[0][1]

    async def test_a_person_who_joins_is_not_sent_a_notice(self, flock) -> None:
        host = await flock.add("host")
        host.broker._room_bridge = SimpleNamespace(
            join_environment=AsyncMock(return_value=_Seat(room_ids=("r",))),
            tell_member=AsyncMock(),
        )

        await host.broker.join_environment(
            participant_id="alice", display_name="Alice", environment_id="e"
        )

        host.broker._room_bridge.tell_member.assert_not_awaited()

    async def test_a_member_that_cannot_be_told_fails_the_join_loudly(self, flock) -> None:
        host = await flock.add("host")
        host.broker._room_bridge = SimpleNamespace(
            join_environment=AsyncMock(return_value=_Seat(room_ids=("r",))),
            tell_member=AsyncMock(side_effect=RuntimeError("no route to the member")),
        )
        host.broker._emit_broker_frame = AsyncMock()

        with pytest.raises(RuntimeError, match="no route to the member"):
            await host.broker.join_environment(
                participant_id="agent-1",
                display_name="Agent",
                environment_id="e",
                session_id="s-9",
            )
