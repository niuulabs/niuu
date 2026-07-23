"""Tests for Skuld's collaboration surface and transport adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.collaboration_adapter import SkuldCollaborationAdapter
from skuld.config import RoomConfig
from sleipnir.domain import registry


def _adapter(**kwargs) -> tuple[SkuldCollaborationAdapter, MagicMock]:
    channels = MagicMock()
    channels.broadcast = AsyncMock()
    adapter = SkuldCollaborationAdapter(
        RoomConfig(
            enabled=True,
            environment_id="cluster-a",
            participant_colors=["p1", "p2"],
            presence_sweep_interval_s=0,
        ),
        channels,
        **kwargs,
    )
    return adapter, channels


def _websocket() -> MagicMock:
    websocket = MagicMock()
    websocket.send_text = AsyncMock()
    return websocket


@pytest.mark.asyncio
async def test_registration_uses_shared_room_and_publishes_presence() -> None:
    publish = AsyncMock()
    adapter, channels = _adapter(publish_presence_event=publish, clock=lambda: 100.0)

    participant = await adapter.register(
        "ravn-1",
        "Resident",
        _websocket(),
        capabilities=["inspect"],
        subscribes_to=["research.*"],
    )

    assert participant.environment_id == "cluster-a"
    assert adapter.participants["ravn-1"] == participant
    assert channels.broadcast.await_args.args[0]["type"] == "participant_joined"
    assert publish.await_args.args[0].event_type == registry.PARTICIPANT_JOINED


@pytest.mark.asyncio
async def test_message_event_renders_persists_and_reports_timeline() -> None:
    append_turn = MagicMock()
    report_timeline = AsyncMock()
    observe = AsyncMock()
    adapter, channels = _adapter(
        append_turn=append_turn,
        report_timeline_event=report_timeline,
        observe_peer_event=observe,
    )
    await adapter.register("ravn-1", "Resident", _websocket())
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "ravn-1",
        {
            "type": "collaboration.events",
            "events": [
                {
                    "kind": "message",
                    "sourceEventType": "response",
                    "content": "Investigation complete",
                    "metadata": {"thread_id": "case-1"},
                    "visibility": "public",
                }
            ],
        },
    )

    message, activity = [call.args[0] for call in channels.broadcast.await_args_list]
    assert message["type"] == "room_message"
    assert message["participantId"] == "ravn-1"
    assert message["threadId"] == "case-1"
    assert activity["activityType"] == "idle"
    assert append_turn.call_args.args[0].content == "Investigation complete"
    assert report_timeline.await_args.args[0]["type"] == "message"
    observe.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_message_preserves_failure_kind_for_channel_coalescing() -> None:
    adapter, channels = _adapter()
    await adapter.register("ravn-1", "Resident", _websocket())
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "ravn-1",
        {
            "events": [
                {
                    "kind": "message",
                    "sourceEventType": "error",
                    "content": "Backend unavailable",
                    "error": True,
                    "failureKind": "LLMError",
                }
            ],
        },
    )

    message = channels.broadcast.await_args_list[0].args[0]
    assert message["type"] == "room_message"
    assert message["failureKind"] == "LLMError"


@pytest.mark.asyncio
async def test_activity_and_agent_event_are_surface_projections_only() -> None:
    timeline = AsyncMock()
    observe = AsyncMock()
    adapter, channels = _adapter(report_timeline_event=timeline, observe_peer_event=observe)
    await adapter.register("ravn-1", "Resident", _websocket())
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "ravn-1",
        {
            "events": [
                {
                    "kind": "activity",
                    "sourceEventType": "tool_start",
                    "activityType": "tool_executing",
                    "detail": {"tool": "shell"},
                },
                {
                    "kind": "agent_event",
                    "sourceEventType": "tool_start",
                    "taskId": "task-1",
                    "event": {
                        "type": "tool_start",
                        "payload": {
                            "tool_name": "BashTool",
                            "input": {"command": "kubectl get pods"},
                        },
                        "urgency": 0.7,
                    },
                    "timeline": {"type": "terminal", "label": "kubectl get pods"},
                },
            ]
        },
    )

    activity, agent_event = [call.args[0] for call in channels.broadcast.await_args_list]
    assert activity["type"] == "room_activity"
    assert json.loads(activity["detail"]) == {"tool": "shell"}
    assert agent_event["type"] == "room_agent_event"
    assert timeline.await_args.args[0]["label"] == "Resident: kubectl get pods"
    observe.assert_awaited_once()
    peer_id, event_type, observation = observe.await_args.args
    assert (peer_id, event_type) == ("ravn-1", "tool_start")
    assert observation["metadata"]["input"] == {"command": "kubectl get pods"}
    assert observation["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_help_notification_preserves_ravn_reply_context_for_operator_resume() -> None:
    observe = AsyncMock()
    adapter, channels = _adapter(observe_peer_event=observe)
    websocket = _websocket()
    await adapter.register("ravn-1", "Resident", websocket)
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "ravn-1",
        {
            "events": [
                {
                    "kind": "notification",
                    "sourceEventId": "help-event-1",
                    "sourceEventType": "help_needed",
                    "notificationType": "help_needed",
                    "summary": "Approval required",
                    "reason": "missing_authority",
                    "replyContext": {"case_id": "case-1"},
                    "traceContext": {"traceparent": "trace-1"},
                }
            ]
        },
    )

    notification = channels.broadcast.await_args.args[0]
    assert notification["notificationType"] == "help_needed"
    assert notification["sourceEventId"] == "help-event-1"
    assert notification["trace_context"] == {"traceparent": "trace-1"}
    assert adapter.pending_help_peer_ids() == ("ravn-1",)
    assert observe.await_args.args[1] == "help_needed"
    assert observe.await_args.args[2]["data"]["reason"] == "missing_authority"

    assert await adapter.route_directed_message("ravn-1", "Approved") is True
    payload = json.loads(websocket.send_text.await_args.args[0])
    assert payload["metadata"]["case_id"] == "case-1"
    assert adapter.pending_help_peer_ids() == ()


@pytest.mark.asyncio
async def test_exact_collaboration_event_redelivery_is_handled_once() -> None:
    observe = AsyncMock()
    adapter, channels = _adapter(observe_peer_event=observe)
    await adapter.register("ravn-1", "Resident", _websocket())
    channels.broadcast.reset_mock()
    event = {
        "kind": "notification",
        "sourceEventId": "help-event-replayed",
        "sourceEventType": "help_needed",
        "notificationType": "help_needed",
        "summary": "Approval required",
        "reason": "missing_authority",
    }

    await adapter.handle_collaboration_frame("ravn-1", {"events": [event]})
    await adapter.handle_collaboration_frame("ravn-1", {"events": [dict(event)]})

    channels.broadcast.assert_awaited_once()
    observe.assert_awaited_once()


@pytest.mark.asyncio
async def test_outcome_is_visible_and_delivered_to_declared_subscribers() -> None:
    observe = AsyncMock()
    adapter, channels = _adapter(observe_peer_event=observe)
    producer = _websocket()
    subscriber = _websocket()
    await adapter.register("producer", "Producer", producer)
    await adapter.register(
        "subscriber",
        "Subscriber",
        subscriber,
        subscribes_to=["research.*"],
    )
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "producer",
        {
            "events": [
                {
                    "kind": "outcome",
                    "sourceEventType": "outcome",
                    "eventType": "research.completed",
                    "fields": {"artifact": "result.md"},
                    "context": {"workflow_parent_event_id": "activation-1"},
                    "valid": True,
                }
            ]
        },
    )

    outcome = channels.broadcast.await_args.args[0]
    assert outcome["type"] == "room_outcome"
    delivered = json.loads(subscriber.send_text.await_args.args[0])
    assert delivered["type"] == "collaboration.outcome"
    assert delivered["fields"] == {"artifact": "result.md"}
    producer.send_text.assert_not_awaited()
    peer_id, event_type, observation = observe.await_args.args
    assert (peer_id, event_type) == ("producer", "outcome")
    assert observation["data"]["workflow_parent_event_id"] == "activation-1"
    assert observation["data"]["fields"] == {"artifact": "result.md"}


@pytest.mark.asyncio
async def test_delegation_is_rendered_without_mesh_protocol_interpretation() -> None:
    adapter, channels = _adapter()
    await adapter.register("ravn-1", "Resident", _websocket())
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "ravn-1",
        {
            "kind": "delegation",
            "sourceEventType": "tool_start",
            "eventType": "code.review",
            "direction": "delegate",
            "preview": "Review this change",
        },
    )

    event = channels.broadcast.await_args.args[0]
    assert event["type"] == "room_mesh_message"
    assert event["eventType"] == "code.review"


@pytest.mark.asyncio
async def test_usage_is_adapted_to_skuld_runtime_accounting() -> None:
    report = AsyncMock()
    adapter, _channels = _adapter(report_usage=report)
    await adapter.register("ravn-1", "Resident", _websocket())

    frame = {
        "kind": "usage",
        "usage": {
            "usage_id": "usage-1",
            "model": "codex",
            "inputTokens": 10,
            "outputTokens": 5,
        },
    }
    await adapter.handle_collaboration_frame("ravn-1", frame)
    await adapter.handle_collaboration_frame("ravn-1", frame)

    report.assert_awaited_once_with(
        {
            "modelUsage": {
                "codex": {
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                }
            }
        }
    )


@pytest.mark.asyncio
async def test_cli_runtime_uses_same_surface_adapter() -> None:
    append_turn = MagicMock()
    adapter, channels = _adapter(append_turn=append_turn)
    await adapter.register_mesh_peer("skuld-cli", "Skuld", participant_type="skuld")
    channels.broadcast.reset_mock()

    await adapter.broadcast_cli_activity("skuld-cli", "thinking", "working")
    await adapter.broadcast_cli_message("skuld-cli", "Done")

    assert channels.broadcast.await_args_list[0].args[0]["type"] == "room_activity"
    assert any(
        call.args[0]["type"] == "room_message" for call in channels.broadcast.await_args_list
    )
    assert append_turn.call_args.args[0].content == "Done"


@pytest.mark.asyncio
async def test_unknown_event_and_unknown_participant_do_not_create_fake_output() -> None:
    adapter, channels = _adapter()

    await adapter.handle_collaboration_frame("missing", {"kind": "message"})
    await adapter.register("ravn-1", "Resident", _websocket())
    channels.broadcast.reset_mock()
    await adapter.handle_collaboration_frame("ravn-1", {"kind": "invented"})

    channels.broadcast.assert_not_awaited()
