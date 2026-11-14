"""An agent from another session sits in a room and takes delivery where it lives."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from niuu.domain.peer_envelope import RelayedSpeech
from niuu.domain.peer_message import PeerAddress, PeerKind, ReplyKind, ReplyRoute
from skuld.broker import ROOM_MEMBER_FRAME
from skuld.collaboration_adapter import SkuldCollaborationAdapter
from skuld.config import RoomConfig
from skuld.room_delivery import RoomDelivery, RoomDeliveryError
from tests.test_skuld.test_broker_review_regressions import _broker

HOST = PeerAddress(kind=PeerKind.SESSION, id="host-session", name="the-host", node="thor")


def _bridge(deliver_to_session=None) -> SkuldCollaborationAdapter:
    channels = MagicMock()
    channels.broadcast = AsyncMock()
    return SkuldCollaborationAdapter(
        config=RoomConfig(enabled=True, participant_colors=["p1", "p2"]),
        channels=channels,
        emit_frame=channels.broadcast,
        publish_presence_event=AsyncMock(),
        deliver_to_session=deliver_to_session,
        host_identity=lambda: HOST,
    )


async def _seat(bridge: SkuldCollaborationAdapter, **overrides):
    return await bridge.join_environment(
        "coordinator:abc",
        display_name="Ticket coordinator",
        environment_id="workflow-session",
        participant_type="ravn",
        session_id="coordinator-session",
        **overrides,
    )


def _delivered(deliver: AsyncMock, index: int = 0) -> tuple[str, RoomDelivery]:
    session_id, delivery = deliver.await_args_list[index].args
    return session_id, delivery


async def test_a_direct_message_reaches_the_session_a_member_joined_from_as_an_envelope():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge, room_id="huddle-1")
    await bridge.join_environment(
        "human:jozef", display_name="Jozef", environment_id="e", room_id="huddle-1"
    )

    delivered = await bridge.route_directed_message(
        "coordinator:abc", "Are you there?", metadata={"participant_id": "human:jozef"}
    )

    assert delivered is True
    session_id, delivery = _delivered(deliver)
    assert session_id == "coordinator-session"
    assert delivery.content == "Are you there?"
    # The participant who spoke is the sender; the host that relays it is stamped by Forge.
    assert delivery.speech.speaker == PeerAddress(
        kind=PeerKind.PERSON, id="human:jozef", name="Jozef"
    )
    assert delivery.speech.room_id == "huddle-1"
    assert delivery.speech.reply.kind is ReplyKind.ROOM
    assert (delivery.speech.reply.target, delivery.speech.reply.node) == ("host-session", "thor")
    assert delivery.request_id.startswith("room-")


async def test_each_delivery_carries_its_own_request_id():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge, room_id="huddle-1")

    await bridge.route_directed_message("coordinator:abc", "one")
    await bridge.route_directed_message("coordinator:abc", "two")

    first, second = _delivered(deliver, 0)[1], _delivered(deliver, 1)[1]
    assert first.request_id != second.request_id


async def test_a_message_nobody_spoke_is_the_hosts_own():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge, room_id="huddle-1")

    await bridge.route_directed_message("coordinator:abc", "Hello")

    assert _delivered(deliver)[1].speech.speaker == HOST


async def test_a_member_session_that_spoke_is_a_session_sender():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge, room_id="huddle-1")
    await bridge.join_environment(
        "worker:xyz",
        display_name="Worker",
        environment_id="e",
        room_id="huddle-1",
        session_id="worker-session",
    )

    await bridge.route_directed_message(
        "coordinator:abc", "Done", metadata={"participant_id": "worker:xyz"}
    )

    assert _delivered(deliver)[1].speech.speaker == PeerAddress(
        kind=PeerKind.SESSION, id="worker-session", name="Worker"
    )


async def test_a_member_in_no_room_cannot_be_given_a_message_without_a_room():
    bridge = _bridge(AsyncMock(return_value=True))
    await _seat(bridge)

    with pytest.raises(RoomDeliveryError, match="in no room"):
        await bridge.route_directed_message("coordinator:abc", "Hello")


async def test_a_member_in_several_rooms_needs_the_message_to_name_one():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge, room_id="huddle-1")
    await _seat(bridge, room_id="huddle-2")

    with pytest.raises(RoomDeliveryError, match="in 2 rooms"):
        await bridge.route_directed_message("coordinator:abc", "Hello")

    await bridge.route_directed_message(
        "coordinator:abc", "Hello", metadata={"room_id": "huddle-2"}
    )
    assert _delivered(deliver)[1].speech.room_id == "huddle-2"


async def test_only_members_from_other_sessions_are_told():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge, room_id="huddle-1")
    await bridge.join_environment("human:jozef", display_name="J", environment_id="e")

    await bridge.tell_joined_sessions("A gate opened")

    session_id, delivery = _delivered(deliver)
    assert (session_id, delivery.content) == ("coordinator-session", "A gate opened")
    assert delivery.speech.speaker == HOST


async def test_a_member_takes_the_outcomes_it_subscribed_to():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge, subscribes_to=["plan.completed"], room_id="huddle-1")
    await bridge.join_environment(
        "publisher:p", display_name="Publisher", environment_id="e", room_id="huddle-1"
    )

    await bridge._deliver_outcome_to_subscribers(
        {
            "eventType": "plan.completed",
            "persona": "publisher",
            "summary": "Recorded",
            "participantId": "publisher:p",
        },
        source_is_mesh=False,
    )
    await bridge._deliver_outcome_to_subscribers(
        {"eventType": "plan.breakdown.drafted", "persona": "planner", "summary": "Drafted"},
        source_is_mesh=False,
    )

    session_id, delivery = _delivered(deliver)
    assert session_id == "coordinator-session"
    assert delivery.content == "publisher reported plan.completed: Recorded"
    assert delivery.speech.speaker.id == "publisher:p"
    assert deliver.await_count == 1


async def test_a_failed_delivery_is_an_error_not_a_quiet_false():
    bridge = _bridge(AsyncMock(return_value=False))
    await _seat(bridge, room_id="huddle-1")

    with pytest.raises(RoomDeliveryError, match="Could not deliver to room member coordinator:abc"):
        await bridge.route_directed_message("coordinator:abc", "Hello")


async def test_every_member_is_tried_before_a_failure_is_raised():
    deliver = AsyncMock(side_effect=[RoomDeliveryError("Forge refused"), True])
    bridge = _bridge(deliver)
    await _seat(bridge, room_id="huddle-1")
    await bridge.join_environment(
        "worker:xyz",
        display_name="Worker",
        environment_id="e",
        room_id="huddle-1",
        session_id="worker-session",
    )

    with pytest.raises(RoomDeliveryError, match="Forge refused"):
        await bridge.tell_joined_sessions("A gate opened")

    assert deliver.await_count == 2


async def test_a_failed_delivery_is_counted(monkeypatch):
    counted: list[dict] = []

    class Telemetry:
        def count(self, name, attributes=None, **_kw):
            counted.append({"name": name, **(attributes or {})})

        def __getattr__(self, name):
            return MagicMock()

    monkeypatch.setattr("skuld.collaboration_adapter.get_observability", lambda: Telemetry())
    bridge = _bridge(AsyncMock(side_effect=RoomDeliveryError("Forge refused")))
    await _seat(bridge, room_id="huddle-1")

    with pytest.raises(RoomDeliveryError):
        await bridge.route_directed_message("coordinator:abc", "Hello")

    assert {"name": "skuld.collaboration.deliveries", "outcome": "failed"} in counted


async def test_a_member_with_no_route_to_its_session_is_a_hard_error():
    bridge = _bridge()
    await _seat(bridge)

    with pytest.raises(RuntimeError, match="no route to another session"):
        await bridge.tell_joined_sessions("A gate opened")


def _room_broker(tmp_path):
    broker = _broker(tmp_path, api_url="")
    broker._room_bridge = _bridge(AsyncMock(return_value=True))
    broker._emit_pipeline_event = AsyncMock()
    broker._start_trace_span = AsyncMock(return_value=None)
    broker._finish_trace_span = AsyncMock()
    broker._report_activity_state = AsyncMock()
    broker.handle_human_room_message = AsyncMock()
    broker._mesh_adapter = SimpleNamespace(publish=AsyncMock())
    return broker


def _delivery(content: str = "Hello") -> RoomDelivery:
    return RoomDelivery(
        content=content,
        speech=RelayedSpeech(
            speaker=PeerAddress(kind=PeerKind.PERSON, id="human:jozef", name="Jozef"),
            room_id="huddle-1",
            reply=ReplyRoute(kind=ReplyKind.ROOM, target="host-session", node="thor"),
        ),
        request_id="room-1",
    )


def _forge_broker(tmp_path, response: httpx.Response, *, token: str = "session-token"):
    broker = _broker(tmp_path, api_url="http://forge")
    broker._settings.forge_mcp.token = token
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    broker._get_http_client = AsyncMock(return_value=client)
    return broker, client


async def test_delivery_goes_through_forge_as_the_hosts_session_with_the_envelope(tmp_path):
    response = httpx.Response(200, request=httpx.Request("POST", "http://forge/x"))
    broker, client = _forge_broker(tmp_path, response)

    assert await broker._deliver_to_member_session("coordinator-session", _delivery()) is True

    url = client.post.await_args.args[0]
    assert url.endswith("/sessions/coordinator-session/messages")
    body = client.post.await_args.kwargs["json"]
    assert body["content"] == "Hello" and body["request_id"] == "room-1"
    assert body["peer"] == {
        "speaker": {"kind": "person", "id": "human:jozef", "name": "Jozef", "node": ""},
        "room_id": "huddle-1",
        "reply": {"kind": "room", "target": "host-session", "node": "thor"},
    }
    # The host's own session credential: Forge stamps relayed_by from it.
    assert client.post.await_args.kwargs["headers"] == {"Authorization": "Bearer session-token"}


async def test_forge_refusing_a_delivery_raises_with_the_remedy(tmp_path):
    response = httpx.Response(
        403, text="missing scope", request=httpx.Request("POST", "http://forge/x")
    )
    broker, _ = _forge_broker(tmp_path, response)

    with pytest.raises(RoomDeliveryError, match=r"\(403\).*'message' grant"):
        await broker._deliver_to_member_session("coordinator-session", _delivery())


async def test_a_host_without_a_session_credential_cannot_relay(tmp_path):
    response = httpx.Response(200, request=httpx.Request("POST", "http://forge/x"))
    broker, client = _forge_broker(tmp_path, response, token="")

    with pytest.raises(RoomDeliveryError, match="forge_mcp.token"):
        await broker._deliver_to_member_session("coordinator-session", _delivery())

    client.post.assert_not_awaited()


async def test_an_unreachable_forge_raises(tmp_path):
    broker, client = _forge_broker(tmp_path, httpx.Response(200))
    client.post = AsyncMock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(RoomDeliveryError, match="Could not reach session"):
        await broker._deliver_to_member_session("coordinator-session", _delivery())


async def test_no_forge_url_raises(tmp_path):
    broker = _broker(tmp_path, api_url="")

    with pytest.raises(RoomDeliveryError, match="volundr_api_url is not set"):
        await broker._deliver_to_member_session("coordinator-session", _delivery())


def _member_frame(*, joined: bool, **extra: str) -> SimpleNamespace:
    payload = {"type": ROOM_MEMBER_FRAME, "joined": joined, "participant_id": "coordinator:abc"}
    if joined:
        payload["member"] = {
            "display_name": "Ticket coordinator",
            "environment_id": "workflow-session",
            "participant_type": "ravn",
            "session_id": "coordinator-session",
            "subscribes_to": ["plan.completed"],
            "notice_context": "Ticket NIU-1, Delivery, run a2a-1",
            **extra,
        }
    return SimpleNamespace(kind=ROOM_MEMBER_FRAME, payload=payload)


@pytest.mark.parametrize("extra", [{}, {"role": "observer"}], ids=["current", "with-retired-role"])
async def test_a_member_keeps_its_seat_when_the_session_restarts(tmp_path, extra):
    broker = _room_broker(tmp_path)
    broker._restore_durable_runtime_state([_member_frame(joined=True, **extra)])

    await broker._rejoin_room_members()

    participant = broker._room_bridge.participant("coordinator:abc")
    assert participant is not None
    assert participant.session_id == "coordinator-session"
    assert "plan.completed" in participant.subscribes_to
    assert participant.notice_context == "Ticket NIU-1, Delivery, run a2a-1"


async def test_a_member_that_left_stays_gone_after_a_restart(tmp_path):
    broker = _room_broker(tmp_path)
    broker._restore_durable_runtime_state([_member_frame(joined=True), _member_frame(joined=False)])

    await broker._rejoin_room_members()

    assert not broker._room_bridge.has_participant("coordinator:abc")


async def test_joining_and_leaving_are_logged_durably(tmp_path):
    broker = _room_broker(tmp_path)
    broker._emit_broker_frame = AsyncMock()

    await broker.join_environment(
        participant_id="coordinator:abc",
        display_name="Ticket coordinator",
        environment_id="workflow-session",
        participant_type="ravn",
        session_id="coordinator-session",
    )
    await broker.leave_environment(participant_id="coordinator:abc", reason="run_finished")

    frames = [call.args[0] for call in broker._emit_broker_frame.await_args_list]
    assert [(frame["type"], frame["joined"]) for frame in frames] == [
        (ROOM_MEMBER_FRAME, True),
        (ROOM_MEMBER_FRAME, False),
    ]


async def test_a_member_session_speaks_in_the_room_as_itself(tmp_path):
    broker = _room_broker(tmp_path)
    broker.handle_human_room_message = AsyncMock(return_value="msg-1")
    await broker._room_bridge.join_environment(
        "worker:xyz",
        display_name="Worker",
        environment_id="e",
        room_id="huddle-1",
        session_id="worker-session",
    )

    assert await broker.handle_session_room_message("worker-session", "huddle-1", "Done") == "msg-1"

    args = broker.handle_human_room_message.await_args
    assert args.args == ("Done",)
    assert args.kwargs["participant_id"] == "worker:xyz"
    assert args.kwargs["source"] == "session"
    assert args.kwargs["metadata"] == {
        "speaker_session_id": "worker-session",
        "room_id": "huddle-1",
    }


@pytest.mark.parametrize(
    ("session_id", "room_id"),
    [("other-session", "huddle-1"), ("worker-session", "huddle-2")],
)
async def test_a_session_that_is_not_seated_in_the_room_is_refused(tmp_path, session_id, room_id):
    broker = _room_broker(tmp_path)
    await broker._room_bridge.join_environment(
        "worker:xyz",
        display_name="Worker",
        environment_id="e",
        room_id="huddle-1",
        session_id="worker-session",
    )

    with pytest.raises(PermissionError, match="not a member of room"):
        await broker.handle_session_room_message(session_id, room_id, "Done")

    broker.handle_human_room_message.assert_not_awaited()


async def test_a_session_message_needs_room_mode(tmp_path):
    broker = _room_broker(tmp_path)
    broker._room_bridge = None

    with pytest.raises(RuntimeError, match="Room mode is not enabled"):
        await broker.handle_session_room_message("worker-session", "huddle-1", "Done")


async def _mesh_member(broker, peer_id: str = "member-session", *, room_ids=("team-1",)):
    return await broker._room_bridge.register_mesh_peer(
        peer_id,
        "Reviewer",
        participant_type="skuld",
        participant_kind="mesh",
        environment_id="team-1",
        room_ids=room_ids,
    )


async def test_a_realm_room_member_on_the_mesh_speaks_in_the_room_as_itself(tmp_path):
    broker = _room_broker(tmp_path)
    broker.handle_human_room_message = AsyncMock(return_value="msg-1")
    await _mesh_member(broker)

    assert await broker.handle_session_room_message("member-session", "team-1", "Done") == "msg-1"

    kwargs = broker.handle_human_room_message.await_args.kwargs
    assert kwargs["participant_id"] == "member-session"
    assert kwargs["metadata"]["speaker_session_id"] == "member-session"


async def test_a_member_whose_mesh_peer_id_is_not_its_session_id_is_known_by_the_verified_peer(
    tmp_path,
):
    broker = _room_broker(tmp_path)
    broker.handle_human_room_message = AsyncMock(return_value="msg-1")
    await _mesh_member(broker, "configured-peer")

    with pytest.raises(PermissionError, match="not a member of room"):
        await broker.handle_session_room_message("member-session", "team-1", "Done")
    assert (
        await broker.handle_session_room_message(
            "member-session", "team-1", "Done", caller_peer_id="configured-peer"
        )
        == "msg-1"
    )


async def test_a_mesh_peer_that_is_not_seated_in_the_room_is_refused(tmp_path):
    broker = _room_broker(tmp_path)
    await _mesh_member(broker, room_ids=())

    with pytest.raises(PermissionError, match="not a member of room"):
        await broker.handle_session_room_message("member-session", "team-1", "Done")


async def test_a_room_host_that_runs_no_agent_does_not_say_a_members_post_to_a_transport(tmp_path):
    broker = _room_broker(tmp_path)
    broker._settings.room.routed = True
    broker.handle_human_room_message = AsyncMock(return_value="msg-1")
    await _mesh_member(broker)

    await broker.handle_session_room_message("member-session", "team-1", "Done")

    assert broker.handle_human_room_message.await_args.kwargs["deliver_to_transport"] is False


async def test_a_host_with_its_own_agent_still_says_a_members_post_to_it(tmp_path):
    broker = _room_broker(tmp_path)
    broker.handle_human_room_message = AsyncMock(return_value="msg-1")
    await _mesh_member(broker)

    await broker.handle_session_room_message("member-session", "team-1", "Done")

    assert broker.handle_human_room_message.await_args.kwargs["deliver_to_transport"] is True
