"""Tests for Skuld's collaboration surface and transport adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.collaboration_adapter import SkuldCollaborationAdapter, outcome_turn_content
from skuld.config import RoomConfig
from sleipnir.domain import registry


def _adapter(**kwargs) -> tuple[SkuldCollaborationAdapter, MagicMock]:
    channels = MagicMock()
    channels.broadcast = AsyncMock()
    emit_frame = kwargs.pop("emit_frame", channels.broadcast)
    adapter = SkuldCollaborationAdapter(
        RoomConfig(
            enabled=True,
            environment_id="cluster-a",
            participant_colors=["p1", "p2"],
            presence_sweep_interval_s=0,
        ),
        channels,
        emit_frame=emit_frame,
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
    assert agent_event["frame"]["data"] == "BashTool"
    assert agent_event["frame"]["metadata"] == {"input": {"command": "kubectl get pods"}}
    assert agent_event["frame"]["payload"]["tool_name"] == "BashTool"
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
async def test_outcome_without_valid_defaults_to_valid() -> None:
    observe = AsyncMock()
    adapter, channels = _adapter(observe_peer_event=observe)
    producer = _websocket()
    await adapter.register("producer", "Producer", producer)
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
                }
            ]
        },
    )

    assert channels.broadcast.await_args.args[0]["valid"] is True
    assert observe.await_args.args[2]["data"]["valid"] is True


@pytest.mark.asyncio
async def test_mesh_outcome_uses_room_delivery_only_for_non_mesh_subscribers() -> None:
    adapter, channels = _adapter()
    producer = _websocket()
    mesh_subscriber = _websocket()
    websocket_subscriber = _websocket()
    await adapter.register_mesh_peer(
        "mesh-producer",
        "Mesh Producer",
        participant_kind="mesh",
    )
    await adapter.register("mesh-producer", "Mesh Producer", producer)
    await adapter.register_mesh_peer(
        "mesh-subscriber",
        "Mesh Subscriber",
        participant_kind="mesh",
        subscribes_to=["research.*"],
    )
    await adapter.register("mesh-subscriber", "Mesh Subscriber", mesh_subscriber)
    await adapter.register(
        "websocket-subscriber",
        "WebSocket Subscriber",
        websocket_subscriber,
        subscribes_to=["research.*"],
    )
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "mesh-producer",
        {
            "kind": "outcome",
            "eventType": "research.completed",
            "fields": {"artifact": "result.md"},
        },
    )

    mesh_subscriber.send_text.assert_not_awaited()
    delivered = json.loads(websocket_subscriber.send_text.await_args.args[0])
    assert delivered["type"] == "collaboration.outcome"
    assert delivered["eventType"] == "research.completed"

    await adapter.handle_collaboration_frame(
        "websocket-subscriber",
        {
            "kind": "outcome",
            "eventType": "research.completed",
            "fields": {"artifact": "channel-result.md"},
        },
    )
    channel_only_delivery = json.loads(mesh_subscriber.send_text.await_args.args[0])
    assert channel_only_delivery["type"] == "collaboration.outcome"
    assert channel_only_delivery["fields"] == {"artifact": "channel-result.md"}

    mesh_subscriber.send_text.reset_mock()
    assert await adapter.route_directed_message("mesh-subscriber", "Please retry") is True
    directed = json.loads(mesh_subscriber.send_text.await_args.args[0])
    assert directed["type"] == "directed_message"
    assert directed["content"] == "Please retry"


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
async def test_a_room_row_is_kept_so_a_later_viewer_sees_it() -> None:
    """A workflow step's offer shown live is also a turn of the room, under the same id."""
    append_turn = MagicMock()
    adapter, channels = _adapter(append_turn=append_turn)
    await adapter.register("root-1", "Coordinator", _websocket())
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "root-1",
        {
            "kind": "delegation",
            "sourceEventType": "workflow",
            "fromPersona": "",
            "eventType": "step.offered",
            "direction": "workflow",
            "preview": "Offered Implement the change to coder",
        },
    )

    live = channels.broadcast.await_args.args[0]
    kept = append_turn.call_args.args[0]
    assert live["type"] == "room_mesh_message"
    assert kept.id == live["id"]
    assert kept.content == "Offered Implement the change to coder"
    assert kept.participant_id == "root-1"
    assert kept.visibility == "public"
    assert kept.metadata == {
        "messageType": "mesh_message",
        "fromPersona": "Coordinator",
        "eventType": "step.offered",
        "direction": "workflow",
    }


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "payload", "data", "metadata"),
    [
        ("thought", {"text": "Checking"}, "Checking", {}),
        ("thought", {"text": "hmm", "thinking": True}, "hmm", {"thinking": True}),
        (
            "tool_result",
            {"tool_name": "BashTool", "result": "3 pods", "is_error": False},
            "3 pods",
            {"tool_name": "BashTool", "is_error": False},
        ),
        ("error", {"message": "Backend unavailable"}, "Backend unavailable", {}),
    ],
)
async def test_agent_event_frame_carries_the_data_envelope_room_clients_render(
    event_type: str, payload: dict, data: object, metadata: dict
) -> None:
    """Room clients stream a participant's turn from ``frame.data``/``frame.metadata``."""
    adapter, channels = _adapter()
    await adapter.register("ravn-1", "Resident", _websocket())
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "ravn-1",
        {
            "events": [
                {
                    "kind": "agent_event",
                    "sourceEventType": event_type,
                    "event": {"type": event_type, "payload": payload, "taskId": "", "urgency": 0.5},
                }
            ]
        },
    )

    frame = channels.broadcast.await_args.args[0]["frame"]
    assert frame["type"] == event_type
    assert frame["data"] == data
    assert frame["metadata"] == metadata
    assert frame["payload"] == payload


def _agent_event(event_type: str, payload: dict) -> dict:
    return {
        "kind": "agent_event",
        "sourceEventType": event_type,
        "event": {"type": event_type, "payload": payload, "taskId": "", "urgency": 0.5},
    }


@pytest.mark.asyncio
async def test_peer_working_steps_are_saved_as_one_internal_turn_before_its_reply() -> None:
    """A session opened later shows the peer's work, not only what it concluded."""
    append_turn = MagicMock()
    adapter, _channels = _adapter(append_turn=append_turn)
    await adapter.register("ravn-1", "Resident", _websocket())

    await adapter.handle_collaboration_frame(
        "ravn-1",
        {
            "events": [
                _agent_event("thought", {"text": "Check "}),
                _agent_event("thought", {"text": "the pods"}),
                _agent_event(
                    "tool_start",
                    {"tool_name": "BashTool", "input": {"command": "kubectl get pods"}},
                ),
                # Ravn reports every tool result as an idle activity; that must not
                # split the turn.
                {"kind": "activity", "activityType": "idle"},
                _agent_event(
                    "tool_result",
                    {"tool_name": "BashTool", "result": "3 pods", "is_error": False},
                ),
                {"kind": "message", "content": "Three pods are running"},
            ]
        },
    )

    work, reply = [call.args[0] for call in append_turn.call_args_list]
    assert work.visibility == "internal"
    assert work.participant_id == "ravn-1"
    assert work.participant_meta["persona"] == "Resident"
    reasoning, tool_use, tool_result = work.parts
    assert reasoning == {"type": "reasoning", "text": "Check the pods"}
    assert tool_use["type"] == "tool_use"
    assert tool_use["name"] == "BashTool"
    assert tool_use["input"] == {"command": "kubectl get pods"}
    assert tool_result == {
        "type": "tool_result",
        "tool_use_id": tool_use["id"],
        "content": "3 pods",
        "is_error": False,
    }
    assert reply.content == "Three pods are running"


@pytest.mark.asyncio
async def test_peer_working_steps_are_saved_when_it_reports_an_outcome() -> None:
    append_turn = MagicMock()
    adapter, _channels = _adapter(append_turn=append_turn)
    await adapter.register("ravn-1", "Framer", _websocket())

    await adapter.handle_collaboration_frame(
        "ravn-1",
        {
            "events": [
                _agent_event("thought", {"text": "Framing the reader"}),
                {"kind": "outcome", "eventType": "spec.framed", "fields": {"summary": "Framed"}},
            ]
        },
    )

    (work,) = [call.args[0] for call in append_turn.call_args_list]
    assert work.visibility == "internal"
    assert work.parts == [{"type": "reasoning", "text": "Framing the reader"}]


@pytest.mark.asyncio
async def test_peer_working_steps_are_saved_when_the_peer_leaves() -> None:
    append_turn = MagicMock()
    adapter, _channels = _adapter(append_turn=append_turn)
    await adapter.register("ravn-1", "Resident", _websocket())
    await adapter.handle_collaboration_frame(
        "ravn-1", {"events": [_agent_event("thought", {"text": "Half way"})]}
    )
    append_turn.assert_not_called()

    await adapter.unregister("ravn-1")

    (work,) = [call.args[0] for call in append_turn.call_args_list]
    assert work.parts == [{"type": "reasoning", "text": "Half way"}]


def _realm_room(**kwargs) -> tuple[SkuldCollaborationAdapter, MagicMock, list]:
    channels = MagicMock()
    channels.broadcast = AsyncMock()
    turns: list = []
    adapter = SkuldCollaborationAdapter(
        RoomConfig(
            enabled=True,
            environment_id="workflow-run-1",
            presence_sweep_interval_s=0,
            realm_room=True,
        ),
        channels,
        emit_frame=channels.broadcast,
        append_turn=turns.append,
        **kwargs,
    )
    return adapter, channels, turns


@pytest.mark.asyncio
async def test_a_realm_room_keeps_a_members_outcome_as_its_turn() -> None:
    adapter, channels, turns = _realm_room()
    await adapter.register_mesh_peer(
        "reviewer-session",
        "reviewer",
        display_name="wf-run-1-reviewer",
        participant_type="skuld",
        participant_kind="mesh",
        heartbeat_ttl_s=0.0,
    )
    channels.broadcast.reset_mock()

    await adapter.handle_collaboration_frame(
        "reviewer-session",
        {
            "events": [
                {
                    "kind": "outcome",
                    "eventType": "code.changed",
                    "summary": "Session completed (2 turns, 0 files)",
                    "fields": {
                        "event_type": "code.changed",
                        "task_description": "the whole brief, which the turn leaves out",
                        "diff_summary": "a long diff",
                        "outcome": {"verdict": "pass", "summary": "Mesh answer matched."},
                        "files_changed": ["probe/hello.txt"],
                    },
                }
            ]
        },
    )

    frames = [call.args[0]["type"] for call in channels.broadcast.await_args_list]
    assert "room_outcome" in frames and "room_message" in frames
    [turn] = [t for t in turns if t.visibility == "public"]
    assert turn.participant_id == "reviewer-session"
    assert turn.participant_meta["display_name"] == "wf-run-1-reviewer"
    assert turn.content.splitlines()[0] == "Session completed (2 turns, 0 files)"
    block = turn.content.split("---outcome---")[1].split("---end---")[0]
    assert "verdict: pass" in block
    assert "event_type: code.changed" in block
    assert 'files_changed: ["probe/hello.txt"]' in block
    assert "task_description" not in block and "diff_summary" not in block


def test_an_outcome_field_of_several_lines_keeps_its_lines() -> None:
    findings = "## Critical\nNone.\n\n## Scope\nTwo new files.\n- tests/a.py:4 is test-only"

    content = outcome_turn_content(
        {
            "summary": "No findings.",
            "verdict": "pass",
            "fields": {"findings": findings, "critical_findings": 0},
        }
    )

    block = content.split("---outcome---\n")[1].split("---end---")[0]
    assert block.splitlines() == [
        "verdict: pass",
        "summary: No findings.",
        "findings: |",
        "  ## Critical",
        "  None.",
        "",
        "  ## Scope",
        "  Two new files.",
        "  - tests/a.py:4 is test-only",
        "critical_findings: 0",
    ]


@pytest.mark.asyncio
async def test_any_other_room_shows_an_outcome_without_keeping_a_turn() -> None:
    turns: list = []
    adapter, channels = _adapter(append_turn=turns.append)
    await adapter.register_mesh_peer("producer", "Producer", participant_kind="mesh")

    await adapter.handle_collaboration_frame(
        "producer",
        {"events": [{"kind": "outcome", "eventType": "research.completed", "fields": {}}]},
    )

    assert [t for t in turns if t.visibility == "public"] == []


@pytest.mark.asyncio
async def test_a_notice_is_the_hosts_message_and_is_shown_once() -> None:
    adapter, channels, turns = _realm_room()
    await adapter.register_mesh_peer(
        "room-session", "Skuld", display_name="Skuld", participant_type="skuld"
    )
    channels.broadcast.reset_mock()

    for _ in range(2):
        await adapter.post_notice(
            "room-session", "coder closed Write the code: pass.", notice_id="step-1:closed"
        )

    messages = [
        call.args[0]
        for call in channels.broadcast.await_args_list
        if call.args[0]["type"] == "room_message"
    ]
    assert len(messages) == 1
    assert messages[0]["metadata"] == {"messageType": "notice"}
    [kept] = [t for t in turns if t.visibility == "public"]
    assert kept.metadata["messageType"] == "notice"
    assert messages[0]["participantId"] == "room-session"
    [turn] = [t for t in turns if t.visibility == "public"]
    assert turn.content == "coder closed Write the code: pass."
    assert turn.metadata["room_notice"] is True


@pytest.mark.asyncio
async def test_a_notice_for_a_room_whose_host_is_not_in_it_is_refused() -> None:
    adapter, _, _ = _realm_room()

    with pytest.raises(LookupError, match="not in the room"):
        await adapter.post_notice("room-session", "x", notice_id="n-1")


def test_only_the_rooms_owner_may_post_a_notice() -> None:
    from niuu.room_access import required_role_for_route

    assert required_role_for_route("POST", "room/notice") == "owner"
