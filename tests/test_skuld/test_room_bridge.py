"""Unit tests for skuld.room_bridge.RoomBridge."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.config import RoomConfig
from skuld.room_bridge import RoomBridge

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry() -> MagicMock:
    """Return a mock ChannelRegistry with async broadcast."""
    reg = MagicMock()
    reg.broadcast = AsyncMock()
    return reg


def _make_bridge(
    colors: list[str] | None = None,
    append_turn=None,
    report_timeline_event=None,
    observe_peer_event=None,
) -> tuple[RoomBridge, MagicMock]:
    registry = _make_registry()
    config = RoomConfig(
        enabled=True,
        participant_colors=colors or ["p1", "p2", "p3"],
    )
    bridge = RoomBridge(
        config=config,
        channels=registry,
        append_turn=append_turn,
        report_timeline_event=report_timeline_event,
        observe_peer_event=observe_peer_event,
    )
    return bridge, registry


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.send_text = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# Registration & color assignment
# ---------------------------------------------------------------------------


class TestParticipantRegistration:
    @pytest.mark.asyncio
    async def test_register_creates_participant(self):
        bridge, registry = _make_bridge()
        ws = _fake_ws()

        meta = await bridge.register("peer-1", "Alice", ws)

        assert meta.peer_id == "peer-1"
        assert meta.persona == "Alice"
        assert meta.participant_type == "ravn"
        assert meta.color in {"p1", "p2", "p3"}
        assert "peer-1" in bridge.participants

    @pytest.mark.asyncio
    async def test_register_broadcasts_participant_joined(self):
        bridge, registry = _make_bridge()
        ws = _fake_ws()

        await bridge.register("peer-1", "Alice", ws)

        registry.broadcast.assert_awaited_once()
        event = registry.broadcast.call_args[0][0]
        assert event["type"] == "participant_joined"
        assert event["participant"]["peer_id"] == "peer-1"

    @pytest.mark.asyncio
    async def test_register_assigns_colors_from_pool(self):
        bridge, registry = _make_bridge(colors=["p1", "p2", "p3"])

        meta1 = await bridge.register("p1", "A", _fake_ws())
        meta2 = await bridge.register("p2", "B", _fake_ws())
        meta3 = await bridge.register("p3", "C", _fake_ws())

        colors = {meta1.color, meta2.color, meta3.color}
        assert colors == {"p1", "p2", "p3"}

    @pytest.mark.asyncio
    async def test_register_cycles_colors_beyond_pool(self):
        bridge, registry = _make_bridge(colors=["p1", "p2"])

        await bridge.register("p1", "A", _fake_ws())
        await bridge.register("p2", "B", _fake_ws())
        meta = await bridge.register("p3", "C", _fake_ws())

        assert meta.color == "p1"  # cycles back

    @pytest.mark.asyncio
    async def test_register_reconnect_updates_persona(self):
        bridge, registry = _make_bridge()
        ws1 = _fake_ws()
        ws2 = _fake_ws()

        meta1 = await bridge.register("peer-1", "Alice", ws1)
        assert meta1.persona == "Alice"

        meta2 = await bridge.register("peer-1", "AliceV2", ws2)

        assert meta2.persona == "AliceV2"
        assert meta2.color == meta1.color  # color is preserved
        assert bridge.participants["peer-1"].persona == "AliceV2"

    @pytest.mark.asyncio
    async def test_register_reconnect_reuses_existing_meta(self):
        bridge, registry = _make_bridge()
        ws1 = _fake_ws()
        ws2 = _fake_ws()

        meta1 = await bridge.register("peer-1", "Alice", ws1)
        registry.broadcast.reset_mock()

        meta2 = await bridge.register("peer-1", "Alice", ws2)

        assert meta1 == meta2
        assert bridge.participant_count == 1
        # Still broadcasts participant_joined on reconnect
        registry.broadcast.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unregister_removes_participant(self):
        bridge, registry = _make_bridge()
        await bridge.register("peer-1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.unregister("peer-1")

        assert "peer-1" not in bridge.participants

    @pytest.mark.asyncio
    async def test_unregister_broadcasts_participant_left(self):
        bridge, registry = _make_bridge()
        await bridge.register("peer-1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.unregister("peer-1")

        registry.broadcast.assert_awaited_once()
        event = registry.broadcast.call_args[0][0]
        assert event["type"] == "participant_left"
        assert event["participantId"] == "peer-1"

    @pytest.mark.asyncio
    async def test_unregister_unknown_peer_is_noop(self):
        bridge, registry = _make_bridge()
        # Should not raise
        await bridge.unregister("nonexistent")
        registry.broadcast.assert_awaited_once()


# ---------------------------------------------------------------------------
# Room state
# ---------------------------------------------------------------------------


class TestRoomState:
    @pytest.mark.asyncio
    async def test_get_room_state_event_empty(self):
        bridge, _ = _make_bridge()
        event = bridge.get_room_state_event()
        assert event["type"] == "room_state"
        assert event["participants"] == []

    @pytest.mark.asyncio
    async def test_get_room_state_event_with_participants(self):
        bridge, _ = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        await bridge.register("p2", "Bob", _fake_ws())

        event = bridge.get_room_state_event()
        assert len(event["participants"]) == 2
        peer_ids = {p["peer_id"] for p in event["participants"]}
        assert peer_ids == {"p1", "p2"}

    @pytest.mark.asyncio
    async def test_room_state_reflects_latest_participant_status(self):
        bridge, _ = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())

        await bridge.handle_ravn_frame(
            "p1", {"type": "task_started", "data": "starting", "metadata": {}}
        )
        await bridge.broadcast_cli_activity("p1", "blocked", "silent for 49s")

        event = bridge.get_room_state_event()
        participant = next(p for p in event["participants"] if p["peer_id"] == "p1")
        assert participant["status"] == "blocked"

    def test_participant_count_empty(self):
        bridge, _ = _make_bridge()
        assert bridge.participant_count == 0

    @pytest.mark.asyncio
    async def test_participant_count_after_register(self):
        bridge, _ = _make_bridge()
        await bridge.register("p1", "A", _fake_ws())
        await bridge.register("p2", "B", _fake_ws())
        assert bridge.participant_count == 2


# ---------------------------------------------------------------------------
# Event translation — response / error → room_message
# ---------------------------------------------------------------------------


class TestResponseFrameTranslation:
    @pytest.mark.asyncio
    async def test_response_frame_broadcasts_room_message(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame("p1", {"type": "response", "data": "Hello!", "metadata": {}})

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        room_msgs = [e for e in calls if e["type"] == "room_message"]
        assert len(room_msgs) == 1
        msg = room_msgs[0]
        assert msg["content"] == "Hello!"
        assert msg["participantId"] == "p1"
        assert msg["participant"]["persona"] == "Alice"
        assert msg["visibility"] == "public"

    @pytest.mark.asyncio
    async def test_error_frame_broadcasts_room_message_with_error_flag(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame("p1", {"type": "error", "data": "boom", "metadata": {}})

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        room_msgs = [e for e in calls if e["type"] == "room_message"]
        assert len(room_msgs) == 1
        assert room_msgs[0]["error"] is True

    @pytest.mark.asyncio
    async def test_response_includes_thread_id_when_present(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame(
            "p1",
            {"type": "response", "data": "Hi", "metadata": {"thread_id": "t-123"}},
        )

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        msg = next(e for e in calls if e["type"] == "room_message")
        assert msg["threadId"] == "t-123"

    @pytest.mark.asyncio
    async def test_response_omits_thread_id_when_absent(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame("p1", {"type": "response", "data": "Hi", "metadata": {}})

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        msg = next(e for e in calls if e["type"] == "room_message")
        assert "threadId" not in msg


# ---------------------------------------------------------------------------
# Event translation — activity types
# ---------------------------------------------------------------------------


class TestActivityFrameTranslation:
    @pytest.mark.asyncio
    async def test_thought_frame_broadcasts_room_activity_thinking(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame(
            "p1", {"type": "thought", "data": "pondering", "metadata": {}}
        )

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        activities = [e for e in calls if e["type"] == "room_activity"]
        assert len(activities) == 1
        assert activities[0]["activityType"] == "thinking"
        assert activities[0]["participantId"] == "p1"

    @pytest.mark.asyncio
    async def test_tool_start_frame_broadcasts_tool_executing(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame(
            "p1", {"type": "tool_start", "data": "BashTool", "metadata": {}}
        )

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        activities = [e for e in calls if e["type"] == "room_activity"]
        assert activities[0]["activityType"] == "tool_executing"

    @pytest.mark.asyncio
    async def test_tool_result_frame_broadcasts_idle(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame("p1", {"type": "tool_result", "data": "ok", "metadata": {}})

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        activities = [e for e in calls if e["type"] == "room_activity"]
        assert activities[0]["activityType"] == "idle"

    @pytest.mark.asyncio
    async def test_task_started_frame_broadcasts_busy(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame(
            "p1", {"type": "task_started", "data": "starting", "metadata": {}}
        )

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        activities = [e for e in calls if e["type"] == "room_activity"]
        assert len(activities) == 1
        assert activities[0]["activityType"] == "busy"
        assert activities[0]["participantId"] == "p1"
        assert bridge.participants["p1"].status == "busy"

    @pytest.mark.asyncio
    async def test_activity_frame_stringifies_non_string_detail(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame(
            "p1",
            {"type": "thought", "data": {"step": "inspect", "target": "README.md"}, "metadata": {}},
        )

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        activities = [e for e in calls if e["type"] == "room_activity"]
        assert len(activities) == 1
        assert activities[0]["detail"] == '{"step": "inspect", "target": "README.md"}'
        assert bridge.participants["p1"].status == "thinking"

    @pytest.mark.asyncio
    async def test_task_started_notifies_peer_observer(self):
        observer = AsyncMock()
        bridge, registry = _make_bridge(observe_peer_event=observer)
        await bridge.register("p1", "Alice", _fake_ws())

        frame = {"type": "task_started", "data": "starting", "metadata": {"task_id": "task-1"}}
        await bridge.handle_ravn_frame("p1", frame)

        observer.assert_awaited_once_with("p1", "task_started", frame)

    @pytest.mark.asyncio
    async def test_decision_frame_broadcasts_thinking(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame(
            "p1", {"type": "decision", "data": "deciding", "metadata": {}}
        )

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        activities = [e for e in calls if e["type"] == "room_activity"]
        assert len(activities) == 1
        assert activities[0]["activityType"] == "thinking"

    @pytest.mark.asyncio
    async def test_unknown_frame_type_is_silently_ignored(self):
        bridge, registry = _make_bridge()
        await bridge.register("p1", "Alice", _fake_ws())
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame(
            "p1", {"type": "unknown_future_type", "data": "x", "metadata": {}}
        )

        # No room_message or room_activity should be broadcast
        for call in registry.broadcast.call_args_list:
            ev = call[0][0]
            assert ev["type"] not in ("room_message", "room_activity")

    @pytest.mark.asyncio
    async def test_frame_from_unknown_peer_is_dropped(self):
        bridge, registry = _make_bridge()
        registry.broadcast.reset_mock()

        await bridge.handle_ravn_frame("ghost", {"type": "response", "data": "hi", "metadata": {}})

        registry.broadcast.assert_not_awaited()


# ---------------------------------------------------------------------------
# History persistence via append_turn callback
# ---------------------------------------------------------------------------


class TestHistoryPersistence:
    @pytest.mark.asyncio
    async def test_response_frame_calls_append_turn(self):
        turns = []
        bridge, _ = _make_bridge(append_turn=turns.append)
        await bridge.register("p1", "Alice", _fake_ws())

        await bridge.handle_ravn_frame("p1", {"type": "response", "data": "Hello", "metadata": {}})

        assert len(turns) == 1
        turn = turns[0]
        assert turn.role == "assistant"
        assert turn.content == "Hello"
        assert turn.participant_id == "p1"
        assert turn.participant_meta["persona"] == "Alice"
        assert turn.visibility == "public"

    @pytest.mark.asyncio
    async def test_activity_frame_does_not_persist_turn(self):
        turns = []
        bridge, _ = _make_bridge(append_turn=turns.append)
        await bridge.register("p1", "Alice", _fake_ws())

        await bridge.handle_ravn_frame(
            "p1", {"type": "thought", "data": "thinking", "metadata": {}}
        )

        assert len(turns) == 0

    @pytest.mark.asyncio
    async def test_no_append_turn_callback_does_not_crash(self):
        bridge, _ = _make_bridge(append_turn=None)
        await bridge.register("p1", "Alice", _fake_ws())

        # Should not raise even without a persistence callback
        await bridge.handle_ravn_frame("p1", {"type": "response", "data": "Hello", "metadata": {}})


class TestTimelineReporting:
    @pytest.mark.asyncio
    async def test_response_frame_reports_message_timeline_event(self):
        report_timeline_event = AsyncMock()
        bridge, _ = _make_bridge(report_timeline_event=report_timeline_event)
        await bridge.register("p1", "coder", _fake_ws())

        await bridge.handle_ravn_frame(
            "p1",
            {"type": "response", "data": "Hello from the coder", "metadata": {}},
        )

        report_timeline_event.assert_awaited()
        event = report_timeline_event.await_args_list[0].args[0]
        assert event["type"] == "message"
        assert event["label"] == "coder: Hello from the coder"
        assert event["t"] >= 0

    @pytest.mark.asyncio
    async def test_error_frame_reports_error_timeline_event(self):
        report_timeline_event = AsyncMock()
        bridge, _ = _make_bridge(report_timeline_event=report_timeline_event)
        await bridge.register("p1", "reviewer", _fake_ws())

        await bridge.handle_ravn_frame("p1", {"type": "error", "data": "boom", "metadata": {}})

        event = report_timeline_event.await_args_list[0].args[0]
        assert event["type"] == "error"
        assert event["label"] == "reviewer: boom"

    @pytest.mark.asyncio
    async def test_file_tool_start_reports_file_timeline_event(self):
        report_timeline_event = AsyncMock()
        bridge, _ = _make_bridge(report_timeline_event=report_timeline_event)
        await bridge.register("p1", "coder", _fake_ws())

        await bridge.handle_ravn_frame(
            "p1",
            {
                "type": "tool_start",
                "data": "Write",
                "metadata": {"input": {"file_path": "README.md"}},
            },
        )

        event = report_timeline_event.await_args_list[0].args[0]
        assert event["type"] == "file"
        assert event["action"] == "created"
        assert event["label"] == "coder: README.md"

    @pytest.mark.asyncio
    async def test_bash_tool_start_reports_terminal_timeline_event(self):
        report_timeline_event = AsyncMock()
        bridge, _ = _make_bridge(report_timeline_event=report_timeline_event)
        await bridge.register("p1", "verifier", _fake_ws())

        await bridge.handle_ravn_frame(
            "p1",
            {
                "type": "tool_start",
                "data": "BashTool",
                "metadata": {"input": {"command": "pytest tests/test_example.py"}},
            },
        )

        event = report_timeline_event.await_args_list[0].args[0]
        assert event["type"] == "terminal"
        assert event["label"] == "verifier: pytest tests/test_example.py"

    @pytest.mark.asyncio
    async def test_failed_tool_result_reports_error_timeline_event(self):
        report_timeline_event = AsyncMock()
        bridge, _ = _make_bridge(report_timeline_event=report_timeline_event)
        await bridge.register("p1", "coder", _fake_ws())

        await bridge.handle_ravn_frame(
            "p1",
            {
                "type": "tool_result",
                "data": "command failed",
                "metadata": {"tool_name": "BashTool", "is_error": True},
            },
        )

        event = report_timeline_event.await_args_list[0].args[0]
        assert event["type"] == "error"
        assert event["label"] == "coder: command failed"


# ---------------------------------------------------------------------------
# CLI participant helpers
# ---------------------------------------------------------------------------


class TestCliParticipantHelpers:
    @pytest.mark.asyncio
    async def test_register_mesh_peer_can_mark_skuld_participant(self):
        bridge, _ = _make_bridge()

        meta = await bridge.register_mesh_peer(
            "skuld-1",
            "coder",
            display_name="skuld",
            participant_type="skuld",
        )

        assert meta.participant_type == "skuld"

    @pytest.mark.asyncio
    async def test_broadcast_cli_activity_emits_room_activity(self):
        bridge, registry = _make_bridge()
        await bridge.register_mesh_peer("skuld-1", "coder", display_name="skuld")
        registry.broadcast.reset_mock()

        await bridge.broadcast_cli_activity("skuld-1", "thinking")

        registry.broadcast.assert_awaited_once()
        event = registry.broadcast.call_args[0][0]
        assert event["type"] == "room_activity"
        assert event["participantId"] == "skuld-1"
        assert event["activityType"] == "thinking"

    @pytest.mark.asyncio
    async def test_broadcast_cli_activity_noop_for_unknown_peer(self):
        bridge, registry = _make_bridge()
        registry.broadcast.reset_mock()

        await bridge.broadcast_cli_activity("ghost", "thinking")

        registry.broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broadcast_cli_message_emits_room_message(self):
        bridge, registry = _make_bridge()
        await bridge.register_mesh_peer("skuld-1", "coder", display_name="skuld")
        registry.broadcast.reset_mock()

        await bridge.broadcast_cli_message("skuld-1", "Hello from CLI")

        calls = [c[0][0] for c in registry.broadcast.call_args_list]
        room_msgs = [e for e in calls if e["type"] == "room_message"]
        assert len(room_msgs) == 1
        msg = room_msgs[0]
        assert msg["content"] == "Hello from CLI"
        assert msg["participantId"] == "skuld-1"
        assert msg["participant"]["persona"] == "coder"
        assert msg["participant"]["color"] in {"p1", "p2", "p3"}

    @pytest.mark.asyncio
    async def test_broadcast_cli_message_noop_for_unknown_peer(self):
        bridge, registry = _make_bridge()
        registry.broadcast.reset_mock()

        await bridge.broadcast_cli_message("ghost", "Hello")

        registry.broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broadcast_cli_message_persists_turn(self):
        turns = []
        bridge, _ = _make_bridge(append_turn=turns.append)
        await bridge.register_mesh_peer("skuld-1", "coder", display_name="skuld")

        await bridge.broadcast_cli_message("skuld-1", "Completed task")

        assert len(turns) == 1
        turn = turns[0]
        assert turn.role == "assistant"
        assert turn.content == "Completed task"
        assert turn.participant_id == "skuld-1"


# ---------------------------------------------------------------------------
# Directed routing
# ---------------------------------------------------------------------------


class TestDirectedRouting:
    @pytest.mark.asyncio
    async def test_route_directed_message_sends_to_target_ws(self):
        bridge, _ = _make_bridge()
        ws = _fake_ws()
        await bridge.register("p1", "Alice", ws)

        result = await bridge.route_directed_message("p1", "Hey Alice!")

        assert result is True
        ws.send_text.assert_awaited_once()
        sent = ws.send_text.call_args[0][0]
        payload = json.loads(sent)
        assert payload["type"] == "directed_message"
        assert payload["content"] == "Hey Alice!"

    @pytest.mark.asyncio
    async def test_route_directed_message_returns_false_for_unknown_target(self):
        bridge, _ = _make_bridge()

        result = await bridge.route_directed_message("ghost", "Hey!")

        assert result is False

    @pytest.mark.asyncio
    async def test_route_directed_message_returns_false_on_send_error(self):
        bridge, _ = _make_bridge()
        ws = _fake_ws()
        ws.send_text.side_effect = RuntimeError("connection closed")
        await bridge.register("p1", "Alice", ws)

        result = await bridge.route_directed_message("p1", "Hey!")

        assert result is False
