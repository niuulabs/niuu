"""Unit tests for skuld.room_models and related ConversationTurn extensions."""

import json
from dataclasses import asdict

import pytest

from skuld.broker import ConversationTurn
from skuld.config import RoomConfig, SkuldSettings
from skuld.room_models import ParticipantMeta, RoomState


class TestParticipantMeta:
    """Tests for the ParticipantMeta frozen dataclass."""

    def test_create_human_participant(self):
        p = ParticipantMeta(
            peer_id="user-123",
            persona="Alice",
            color="p1",
            participant_type="human",
        )
        assert p.peer_id == "user-123"
        assert p.persona == "Alice"
        assert p.color == "p1"
        assert p.participant_type == "human"
        assert p.gateway_url is None

    def test_create_ravn_participant(self):
        p = ParticipantMeta(
            peer_id="agent-456",
            persona="Ravn",
            color="p2",
            participant_type="ravn",
            gateway_url="wss://gateway.example.com/ravn",
        )
        assert p.peer_id == "agent-456"
        assert p.participant_type == "ravn"
        assert p.gateway_url == "wss://gateway.example.com/ravn"

    def test_frozen_immutability(self):
        p = ParticipantMeta(
            peer_id="user-1",
            persona="Bob",
            color="p3",
            participant_type="human",
        )
        with pytest.raises((AttributeError, TypeError)):
            p.peer_id = "other"  # type: ignore[misc]

    def test_equality(self):
        p1 = ParticipantMeta(peer_id="x", persona="X", color="p4", participant_type="human")
        p2 = ParticipantMeta(peer_id="x", persona="X", color="p4", participant_type="human")
        assert p1 == p2

    def test_inequality(self):
        p1 = ParticipantMeta(peer_id="x", persona="X", color="p4", participant_type="human")
        p2 = ParticipantMeta(peer_id="y", persona="Y", color="p4", participant_type="human")
        assert p1 != p2


class TestRoomState:
    """Tests for the RoomState frozen dataclass."""

    def test_empty_room(self):
        room = RoomState()
        assert room.participants == {}

    def test_room_with_participants(self):
        p1 = ParticipantMeta(peer_id="u1", persona="Alice", color="p1", participant_type="human")
        p2 = ParticipantMeta(peer_id="a1", persona="Bot", color="p2", participant_type="ravn")
        room = RoomState(participants={"u1": p1, "a1": p2})
        assert len(room.participants) == 2
        assert room.participants["u1"].persona == "Alice"
        assert room.participants["a1"].participant_type == "ravn"

    def test_frozen_immutability(self):
        room = RoomState()
        with pytest.raises((AttributeError, TypeError)):
            room.participants = {}  # type: ignore[misc]


class TestRoomConfig:
    """Tests for the RoomConfig pydantic model."""

    def test_defaults(self):
        config = RoomConfig()
        assert config.enabled is False
        assert config.max_participants == 8
        assert len(config.participant_colors) == 7

    def test_all_participant_slots_present(self):
        config = RoomConfig()
        expected = {"p1", "p2", "p3", "p4", "p5", "p6", "p7"}
        assert set(config.participant_colors) == expected

    def test_override_enabled(self):
        config = RoomConfig(enabled=True)
        assert config.enabled is True

    def test_override_max_participants(self):
        config = RoomConfig(max_participants=4)
        assert config.max_participants == 4

    def test_custom_colors(self):
        config = RoomConfig(participant_colors=["p1", "p2"])
        assert config.participant_colors == ["p1", "p2"]

    def test_skuld_settings_has_room(self):
        settings = SkuldSettings()
        assert hasattr(settings, "room")
        assert isinstance(settings.room, RoomConfig)
        assert settings.room.enabled is False


class TestConversationTurnParticipantFields:
    """Tests for the new optional participant fields on ConversationTurn."""

    def test_defaults_are_none(self):
        turn = ConversationTurn(id="t1", role="user", content="Hello")
        assert turn.participant_id is None
        assert turn.participant_meta is None
        assert turn.thread_id is None
        assert turn.visibility == "public"

    def test_with_participant_fields(self):
        turn = ConversationTurn(
            id="t2",
            role="assistant",
            content="Hi",
            participant_id="agent-1",
            participant_meta={"peer_id": "agent-1", "persona": "Bot", "color": "p2"},
            thread_id="thread-abc",
            visibility="internal",
        )
        assert turn.participant_id == "agent-1"
        assert turn.participant_meta == {"peer_id": "agent-1", "persona": "Bot", "color": "p2"}
        assert turn.thread_id == "thread-abc"
        assert turn.visibility == "internal"

    def test_round_trip_serialization_without_participant(self):
        turn = ConversationTurn(id="t3", role="user", content="Test")
        d = asdict(turn)
        assert d["participant_id"] is None
        assert d["participant_meta"] is None
        assert d["thread_id"] is None
        assert d["visibility"] == "public"

    def test_round_trip_serialization_with_participant(self):
        turn = ConversationTurn(
            id="t4",
            role="user",
            content="Hello",
            participant_id="u1",
            participant_meta={"peer_id": "u1", "persona": "Alice", "color": "p1"},
            thread_id="thr-1",
            visibility="public",
        )
        d = asdict(turn)
        assert d["participant_id"] == "u1"
        assert d["participant_meta"]["persona"] == "Alice"
        assert d["thread_id"] == "thr-1"
        assert d["visibility"] == "public"

    def test_json_serializable(self):
        turn = ConversationTurn(
            id="t5",
            role="assistant",
            content="Answer",
            participant_id="bot-1",
            participant_meta={"peer_id": "bot-1", "persona": "Ravn", "color": "p2"},
        )
        # Should serialize without errors
        payload = json.dumps(asdict(turn))
        reloaded = json.loads(payload)
        assert reloaded["participant_id"] == "bot-1"
        assert reloaded["participant_meta"]["persona"] == "Ravn"

    def test_existing_tests_unchanged(self):
        """Verify existing fields are still present and unmodified."""
        turn = ConversationTurn(
            id="old",
            role="assistant",
            content="legacy",
            parts=[{"type": "text", "text": "legacy"}],
            metadata={"cost": 0.01},
        )
        assert turn.id == "old"
        assert turn.role == "assistant"
        assert turn.content == "legacy"
        assert turn.parts == [{"type": "text", "text": "legacy"}]
        assert turn.metadata == {"cost": 0.01}
