"""Unit tests for skuld.room_mesh_bridge.RoomMeshBridge.

Covers:
- Lifecycle (start / stop / context manager / idempotent calls)
- Session filtering (correlation_id and ravn_session_id)
- OUTCOME events → room_outcome via RoomBridge
- Non-OUTCOME events → room_activity via RoomBridge
- Auto-registration of unknown mesh peers
- _extract_peer_id and _extract_persona helpers
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.room_mesh_bridge import (
    MESH_PATTERNS,
    RoomMeshBridge,
    _extract_peer_id,
    _extract_persona,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)


def _make_event(**kwargs) -> SleipnirEvent:
    defaults: dict = dict(
        event_type="ravn.mesh.code.changed",
        source="ravn:skuld-01",
        payload={
            "ravn_event": {
                "event_type": "code.changed",
                "persona": "coder",
                "source_peer_id": "skuld-01",
                "output": "done",
            },
            "ravn_type": "RavnEventType.OUTCOME",
            "ravn_source": "skuld-01",
            "ravn_urgency": 0.8,
            "ravn_session_id": "sess-abc",
            "ravn_task_id": None,
        },
        summary="Mesh event: code.changed",
        urgency=0.8,
        domain="code",
        timestamp=_TS,
        correlation_id="sess-abc",
    )
    defaults.update(kwargs)
    return SleipnirEvent(**defaults)


def _make_room_bridge(known_peers: list[str] | None = None) -> MagicMock:
    """Return a mock RoomBridge."""
    bridge = MagicMock()
    bridge.handle_ravn_frame = AsyncMock()
    bridge.register_mesh_peer = AsyncMock()
    participants = {}
    for pid in known_peers or []:
        persona = pid.split("-", 1)[-1] if "-" in pid else pid
        display_name = persona
        participant_type = "ravn"
        if pid.startswith("skuld-"):
            persona = "coder"
            display_name = "skuld"
            participant_type = "skuld"
        participants[pid] = MagicMock(
            peer_id=pid,
            persona=persona,
            display_name=display_name,
            participant_type=participant_type,
        )
    bridge.participants = participants
    bridge.has_participant = MagicMock(side_effect=lambda pid: pid in bridge.participants)
    return bridge


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestExtractPeerId:
    def test_from_source_field_fallback(self):
        evt = _make_event(payload={}, source="ravn:peer-99")
        assert _extract_peer_id(evt) == "peer-99"

    def test_source_without_prefix(self):
        evt = _make_event(payload={}, source="plain-peer")
        assert _extract_peer_id(evt) == "plain-peer"

    def test_prefers_mesh_source_over_nested_ravn_source(self):
        evt = _make_event(
            source="ravn:flock-coder",
            payload={"ravn_source": "ravn-084b245b"},
        )
        assert _extract_peer_id(evt) == "flock-coder"

    def test_falls_back_to_ravn_source_when_mesh_source_missing(self):
        evt = _make_event(payload={"ravn_source": "skuld:skuld-01"}, source="")
        assert _extract_peer_id(evt) == "skuld-01"

    def test_empty_source(self):
        evt = _make_event(payload={}, source="")
        assert _extract_peer_id(evt) == ""


class TestExtractPersona:
    def test_persona_from_ravn_event(self):
        evt = _make_event(payload={"ravn_event": {"persona": "reviewer"}})
        assert _extract_persona(evt, "peer-01") == "reviewer"

    def test_persona_fallback_to_peer_id(self):
        evt = _make_event(payload={"ravn_event": {}})
        assert _extract_persona(evt, "peer-01") == "peer-01"

    def test_no_ravn_event(self):
        evt = _make_event(payload={})
        assert _extract_persona(evt, "fallback") == "fallback"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestRoomMeshBridgeLifecycle:
    @pytest.mark.asyncio
    async def test_start_subscribes(self):
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=_make_room_bridge())
        await bridge.start()
        assert bridge._subscription is not None
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_stop_unsubscribes(self):
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=_make_room_bridge())
        await bridge.start()
        await bridge.stop()
        assert bridge._subscription is None

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=_make_room_bridge())
        await bridge.stop()  # must not raise

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self):
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=_make_room_bridge())
        await bridge.start()
        first_sub = bridge._subscription
        await bridge.start()  # second call ignored
        assert bridge._subscription is first_sub
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        bus = InProcessBus()
        async with RoomMeshBridge(subscriber=bus, room_bridge=_make_room_bridge()) as b:
            assert b._subscription is not None
        assert b._subscription is None

    @pytest.mark.asyncio
    async def test_default_patterns_are_mesh_patterns(self):
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=_make_room_bridge())
        assert bridge._patterns == MESH_PATTERNS

    @pytest.mark.asyncio
    async def test_custom_patterns_accepted(self):
        bus = InProcessBus()
        custom = ["ravn.mesh.code.*"]
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=_make_room_bridge(),
            patterns=custom,
        )
        assert bridge._patterns == custom


# ---------------------------------------------------------------------------
# Session filtering
# ---------------------------------------------------------------------------


class TestSessionFiltering:
    @pytest.mark.asyncio
    async def test_matching_correlation_id_passes(self):
        room = _make_room_bridge()
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=room, session_id="sess-abc")
        await bridge.start()

        evt = _make_event(correlation_id="sess-abc")
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_matching_ravn_session_id_passes(self):
        room = _make_room_bridge()
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=room, session_id="sess-abc")
        await bridge.start()

        evt = _make_event(
            correlation_id="other",
            payload={
                "ravn_event": {"persona": "coder"},
                "ravn_type": "RavnEventType.OUTCOME",
                "ravn_source": "skuld-01",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.5,
                "ravn_task_id": None,
            },
        )
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_non_matching_session_is_dropped(self):
        room = _make_room_bridge()
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=room, session_id="sess-abc")
        await bridge.start()

        evt = _make_event(
            correlation_id="sess-OTHER",
            payload={
                "ravn_event": {},
                "ravn_type": "RavnEventType.OUTCOME",
                "ravn_source": "skuld-01",
                "ravn_session_id": "sess-OTHER",
                "ravn_urgency": 0.5,
                "ravn_task_id": None,
            },
        )
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_not_awaited()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_matching_root_correlation_id_passes(self):
        room = _make_room_bridge()
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=room, session_id="sess-abc")
        await bridge.start()

        evt = _make_event(
            correlation_id="event_code_changed_sess",
            payload={
                "ravn_event": {"persona": "reviewer"},
                "ravn_type": "RavnEventType.OUTCOME",
                "ravn_source": "flock-reviewer",
                "ravn_session_id": "",
                "ravn_root_correlation_id": "sess-abc",
                "ravn_urgency": 0.5,
                "ravn_task_id": "event_code_changed_sess",
            },
        )
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_no_session_filter_passes_all(self):
        room = _make_room_bridge()
        bus = InProcessBus()
        bridge = RoomMeshBridge(subscriber=bus, room_bridge=room, session_id=None)
        await bridge.start()

        evt = _make_event(correlation_id="any-session")
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        await bridge.stop()


# ---------------------------------------------------------------------------
# Event translation
# ---------------------------------------------------------------------------


class TestOutcomeTranslation:
    @pytest.mark.asyncio
    async def test_outcome_event_calls_handle_ravn_frame(self):
        room = _make_room_bridge(known_peers=["skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event()  # OUTCOME event
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        call_args = room.handle_ravn_frame.call_args
        peer_id, frame = call_args[0]
        assert peer_id == "skuld-01"
        assert frame["type"] == "outcome"

    @pytest.mark.asyncio
    async def test_outcome_frame_includes_event_type(self):
        room = _make_room_bridge(known_peers=["skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event()
        await bridge._handle_event(evt)

        _, frame = room.handle_ravn_frame.call_args[0]
        assert frame["metadata"]["event_type"] == "code.changed"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_outcome_data_is_ravn_event_payload(self):
        ravn_payload = {
            "event_type": "code.changed",
            "persona": "reviewer",
            "output": "lgtm",
        }
        room = _make_room_bridge(known_peers=["skuld-02"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            source="ravn:skuld-02",
            payload={
                "ravn_event": ravn_payload,
                "ravn_type": "RavnEventType.OUTCOME",
                "ravn_source": "skuld-02",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.5,
                "ravn_task_id": None,
            },
        )
        await bridge._handle_event(evt)

        _, frame = room.handle_ravn_frame.call_args[0]
        assert frame["data"] == ravn_payload
        await bridge.stop()


class TestActivityTranslation:
    @pytest.mark.asyncio
    async def test_tool_start_event_translated_to_tool_start_frame(self):
        room = _make_room_bridge(known_peers=["skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            payload={
                "ravn_event": {"tool_name": "BashTool", "input": {"command": "ls"}},
                "ravn_type": "RavnEventType.TOOL_START",
                "ravn_source": "skuld-01",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.4,
                "ravn_task_id": None,
            }
        )
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        _, frame = room.handle_ravn_frame.call_args[0]
        assert frame["type"] == "tool_start"
        assert frame["data"] == "BashTool"
        assert frame["metadata"]["input"] == {"command": "ls"}
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_thought_event_translated_to_thought_frame(self):
        room = _make_room_bridge(known_peers=["skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            payload={
                "ravn_event": {"text": "Thinking about the README"},
                "ravn_type": "RavnEventType.THOUGHT",
                "ravn_source": "skuld-01",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.3,
                "ravn_task_id": None,
            }
        )
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        _, frame = room.handle_ravn_frame.call_args[0]
        assert frame["type"] == "thought"
        assert frame["data"] == "Thinking about the README"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_response_event_translated_to_response_frame(self):
        room = _make_room_bridge(known_peers=["skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            payload={
                "ravn_event": {"text": "Here is the answer"},
                "ravn_type": "RavnEventType.RESPONSE",
                "ravn_source": "skuld-01",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.3,
                "ravn_task_id": None,
            }
        )
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        _, frame = room.handle_ravn_frame.call_args[0]
        assert frame["type"] == "response"
        assert frame["data"] == "Here is the answer"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_tool_result_event_translated_to_tool_result_frame(self):
        room = _make_room_bridge(known_peers=["skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            payload={
                "ravn_event": {
                    "tool_name": "BashTool",
                    "result": "README.md\nsrc\n",
                    "is_error": False,
                },
                "ravn_type": "RavnEventType.TOOL_RESULT",
                "ravn_source": "skuld-01",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.3,
                "ravn_task_id": None,
            }
        )
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_awaited_once()
        _, frame = room.handle_ravn_frame.call_args[0]
        assert frame["type"] == "tool_result"
        assert frame["data"] == "README.md\nsrc\n"
        assert frame["metadata"]["tool_name"] == "BashTool"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_unknown_ravn_type_is_ignored(self):
        room = _make_room_bridge(known_peers=["skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            payload={
                "ravn_event": {},
                "ravn_type": "RavnEventType.DECISION",
                "ravn_source": "skuld-01",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.3,
                "ravn_task_id": None,
            }
        )
        await bridge._handle_event(evt)

        room.handle_ravn_frame.assert_not_awaited()
        await bridge.stop()


# ---------------------------------------------------------------------------
# Auto-registration of mesh peers
# ---------------------------------------------------------------------------


class TestPeerAutoRegistration:
    @pytest.mark.asyncio
    async def test_unknown_peer_is_auto_registered(self):
        room = _make_room_bridge(known_peers=[])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event()  # peer "skuld-01" not in room
        await bridge._handle_event(evt)

        room.register_mesh_peer.assert_awaited_once()
        call_kwargs = room.register_mesh_peer.call_args[1]
        assert call_kwargs["peer_id"] == "skuld-01"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_known_peer_is_not_re_registered(self):
        room = _make_room_bridge(known_peers=["skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event()  # "skuld-01" already registered
        await bridge._handle_event(evt)

        room.register_mesh_peer.assert_not_awaited()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_internal_drive_loop_source_maps_to_matching_ravn_peer(self):
        room = _make_room_bridge(known_peers=["flock-reviewer", "skuld-01"])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            payload={
                "ravn_event": {"persona": "reviewer", "event_type": "review.completed"},
                "ravn_type": "RavnEventType.OUTCOME",
                "ravn_source": "drive_loop",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.6,
                "ravn_task_id": None,
            },
            source="ravn:drive_loop",
        )
        await bridge._handle_event(evt)

        room.register_mesh_peer.assert_not_awaited()
        room.handle_ravn_frame.assert_awaited_once()
        assert room.handle_ravn_frame.call_args[0][0] == "flock-reviewer"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_auto_registered_peer_has_correct_persona(self):
        room = _make_room_bridge(known_peers=[])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            payload={
                "ravn_event": {"persona": "reviewer", "event_type": "code.reviewed"},
                "ravn_type": "RavnEventType.OUTCOME",
                "ravn_source": "ravn-reviewer-01",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.6,
                "ravn_task_id": None,
            },
            source="ravn:ravn-reviewer-01",
        )
        await bridge._handle_event(evt)

        call_kwargs = room.register_mesh_peer.call_args[1]
        assert call_kwargs["persona"] == "reviewer"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_auto_registered_peer_uses_mesh_peer_id_not_nested_ravn_source(self):
        room = _make_room_bridge(known_peers=[])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        evt = _make_event(
            source="ravn:flock-coder",
            payload={
                "ravn_event": {"persona": "coder", "text": "Thinking"},
                "ravn_type": "RavnEventType.THOUGHT",
                "ravn_source": "ravn-084b245b",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.3,
                "ravn_task_id": None,
            },
        )
        await bridge._handle_event(evt)

        call_kwargs = room.register_mesh_peer.call_args[1]
        assert call_kwargs["peer_id"] == "flock-coder"
        assert call_kwargs["persona"] == "coder"
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_empty_peer_id_is_skipped(self):
        room = _make_room_bridge(known_peers=[])
        bus = InProcessBus()
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        # Event with no source information
        evt = _make_event(
            payload={
                "ravn_event": {},
                "ravn_type": "RavnEventType.OUTCOME",
                "ravn_source": "",
                "ravn_session_id": "sess-abc",
                "ravn_urgency": 0.5,
                "ravn_task_id": None,
            },
            source="",
        )
        await bridge._handle_event(evt)

        room.register_mesh_peer.assert_not_awaited()
        room.handle_ravn_frame.assert_not_awaited()
        await bridge.stop()


# ---------------------------------------------------------------------------
# Integration: InProcessBus end-to-end
# ---------------------------------------------------------------------------


class TestInProcessBusIntegration:
    @pytest.mark.asyncio
    async def test_outcome_event_published_to_bus_reaches_room_bridge(self):
        """Full round-trip: publish to bus → RoomMeshBridge → RoomBridge."""
        import asyncio

        bus = InProcessBus()
        room = _make_room_bridge(known_peers=["skuld-01"])
        bridge = RoomMeshBridge(
            subscriber=bus,
            room_bridge=room,
            session_id="sess-abc",
        )
        await bridge.start()

        # Publish a mesh outcome event directly to the Sleipnir bus
        evt = _make_event()
        await bus.publish(evt)

        # Give the asyncio event loop a tick to process the subscriber queue
        await asyncio.sleep(0)

        room.handle_ravn_frame.assert_awaited_once()
        await bridge.stop()
