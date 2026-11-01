"""An agent from another session sits in a room and takes delivery where it lives."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from skuld.collaboration_adapter import SkuldCollaborationAdapter
from skuld.config import RoomConfig
from skuld.workflow_runtime import ROOM_MEMBER_FRAME, WorkflowGateNode
from tests.test_skuld.test_broker_review_regressions import _broker


def _bridge(deliver_to_session=None) -> SkuldCollaborationAdapter:
    channels = MagicMock()
    channels.broadcast = AsyncMock()
    return SkuldCollaborationAdapter(
        config=RoomConfig(enabled=True, participant_colors=["p1", "p2"]),
        channels=channels,
        emit_frame=channels.broadcast,
        publish_presence_event=AsyncMock(),
        deliver_to_session=deliver_to_session,
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


async def test_a_direct_message_reaches_the_session_a_member_joined_from():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge)

    delivered = await bridge.route_directed_message("coordinator:abc", "Are you there?")

    assert delivered is True
    deliver.assert_awaited_once_with("coordinator-session", "Are you there?")


async def test_only_members_from_other_sessions_are_told():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge)
    await bridge.join_environment("human:jozef", display_name="J", environment_id="e")

    await bridge.tell_joined_sessions("A gate opened")

    deliver.assert_awaited_once_with("coordinator-session", "A gate opened")


async def test_a_member_takes_the_outcomes_it_subscribed_to():
    deliver = AsyncMock(return_value=True)
    bridge = _bridge(deliver)
    await _seat(bridge, subscribes_to=["plan.completed"])

    await bridge._deliver_outcome_to_subscribers(
        {"eventType": "plan.completed", "persona": "publisher", "summary": "Recorded"},
        source_is_mesh=False,
    )
    await bridge._deliver_outcome_to_subscribers(
        {"eventType": "plan.breakdown.drafted", "persona": "planner", "summary": "Drafted"},
        source_is_mesh=False,
    )

    deliver.assert_awaited_once_with(
        "coordinator-session", "publisher reported plan.completed: Recorded"
    )


async def test_a_failed_delivery_is_an_error_and_reports_undelivered(caplog):
    bridge = _bridge(AsyncMock(return_value=False))
    await _seat(bridge)

    delivered = await bridge.route_directed_message("coordinator:abc", "Hello")

    assert delivered is False
    assert "Could not deliver to room member coordinator:abc" in caplog.text


async def test_a_member_with_no_route_to_its_session_is_a_hard_error():
    bridge = _bridge()
    await _seat(bridge)

    with pytest.raises(RuntimeError, match="no route to another session"):
        await bridge.tell_joined_sessions("A gate opened")


async def test_a_joined_agent_cannot_approve_a_gate():
    bridge = _bridge(AsyncMock(return_value=True))
    await _seat(bridge)

    with pytest.raises(PermissionError, match="lacks capability: approve"):
        bridge.require_participant_capability("coordinator:abc", "approve")


def _gate_node() -> WorkflowGateNode:
    return WorkflowGateNode(
        node_id="review",
        label="Code review",
        condition="",
        event_types=["review.done"],
        approval_event_type="review.approved",
        changes_requested_event_type="review.changes",
    )


def _room_broker(tmp_path):
    broker = _broker(tmp_path, api_url="")
    broker._room_bridge = _bridge(AsyncMock(return_value=True))
    broker._emit_pipeline_event = AsyncMock()
    broker._start_trace_span = AsyncMock(return_value=None)
    broker._finish_trace_span = AsyncMock()
    broker._emit_workflow_gate_help_needed_sleipnir_event = AsyncMock()
    broker._report_activity_state = AsyncMock()
    broker.handle_human_room_message = AsyncMock()
    broker._mesh_adapter = SimpleNamespace(publish=AsyncMock())
    return broker


async def test_a_gate_opening_reaches_a_member_and_names_its_ticket_and_run(tmp_path):
    broker = _room_broker(tmp_path)
    deliver = broker._room_bridge._deliver_to_session
    await _seat(broker._room_bridge, notice_context="Ticket NIU-1, Delivery, run a2a-1")

    await broker._activate_workflow_gate(
        _gate_node(), "activation", "review.done", summary="Ready for you."
    )

    session, message = deliver.await_args.args
    assert session == "coordinator-session"
    assert message.startswith("[Ticket NIU-1, Delivery, run a2a-1] ")
    assert "Code review" in message
    assert "waiting for a person's decision" in message
    assert "Ready for you." in message


async def test_a_decided_gate_sends_nothing_that_could_queue_up_stale(tmp_path):
    broker = _room_broker(tmp_path)
    deliver = broker._room_bridge._deliver_to_session
    await _seat(broker._room_bridge)
    state = await broker._activate_workflow_gate(_gate_node(), "activation", "review.done")
    deliver.reset_mock()

    await broker.resolve_workflow_gate(state.id, "APPROVE")

    deliver.assert_not_awaited()


async def test_a_session_without_joined_members_sends_nothing(tmp_path):
    broker = _room_broker(tmp_path)

    await broker._activate_workflow_gate(_gate_node(), "activation", "review.done")

    broker._room_bridge._deliver_to_session.assert_not_awaited()


async def test_delivery_goes_through_forge_to_the_members_session(tmp_path):
    broker = _broker(tmp_path, api_url="http://forge")
    response = httpx.Response(200, request=httpx.Request("POST", "http://forge/x"))
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    broker._get_http_client = AsyncMock(return_value=client)

    assert await broker._deliver_to_member_session("coordinator-session", "Hello") is True

    url = client.post.await_args.args[0]
    assert url.endswith("/sessions/coordinator-session/messages")
    assert client.post.await_args.kwargs["json"] == {"content": "Hello"}


async def test_forge_refusing_a_delivery_reports_it_failed(tmp_path, caplog):
    broker = _broker(tmp_path, api_url="http://forge")
    response = httpx.Response(403, request=httpx.Request("POST", "http://forge/x"))
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    broker._get_http_client = AsyncMock(return_value=client)

    assert await broker._deliver_to_member_session("coordinator-session", "Hello") is False
    assert "Could not reach session coordinator-session" in caplog.text


def _member_frame(*, joined: bool) -> SimpleNamespace:
    payload = {"type": ROOM_MEMBER_FRAME, "joined": joined, "participant_id": "coordinator:abc"}
    if joined:
        payload["member"] = {
            "display_name": "Ticket coordinator",
            "environment_id": "workflow-session",
            "role": "observer",
            "participant_type": "ravn",
            "session_id": "coordinator-session",
            "subscribes_to": ["plan.completed"],
            "notice_context": "Ticket NIU-1, Delivery, run a2a-1",
        }
    return SimpleNamespace(kind=ROOM_MEMBER_FRAME, payload=payload)


async def test_a_member_keeps_its_seat_when_the_session_restarts(tmp_path):
    broker = _room_broker(tmp_path)
    broker._restore_durable_runtime_state([_member_frame(joined=True)])

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
