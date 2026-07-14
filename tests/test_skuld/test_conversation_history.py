"""Tests for conversation history tracking and persistence."""

import json
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from skuld.broker import (
    CONVERSATION_HISTORY_DIR,
    Broker,
    ConversationTurn,
    app,
    broker,
)
from skuld.config import SkuldSettings
from skuld.transports import TransportCapabilities


class TestConversationTurn:
    """Tests for ConversationTurn dataclass."""

    def test_create_turn(self):
        turn = ConversationTurn(
            id="test-id",
            role="user",
            content="Hello",
        )
        assert turn.id == "test-id"
        assert turn.role == "user"
        assert turn.content == "Hello"
        assert turn.parts == []
        assert turn.metadata == {}
        assert turn.created_at  # auto-generated

    def test_turn_to_dict(self):
        turn = ConversationTurn(
            id="test-id",
            role="assistant",
            content="Hi there",
            parts=[{"type": "text", "text": "Hi there"}],
            metadata={"model": "claude-sonnet-4-20250514"},
        )
        d = asdict(turn)
        assert d["id"] == "test-id"
        assert d["role"] == "assistant"
        assert d["content"] == "Hi there"
        assert d["parts"] == [{"type": "text", "text": "Hi there"}]
        assert d["metadata"]["model"] == "claude-sonnet-4-20250514"


class TestConversationHistoryPersistence:
    """Tests for file-based conversation history persistence."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session", "workspace_dir": str(tmp_path)},
            transport="subprocess",
        )
        return Broker(settings=settings)

    def test_history_path(self, test_broker, tmp_path):
        sid = test_broker.session_id
        expected = tmp_path / CONVERSATION_HISTORY_DIR / f"conversation_{sid}.json"
        assert test_broker._conversation_history_path() == expected

    def test_save_and_load_history(self, test_broker):
        turn1 = ConversationTurn(id="1", role="user", content="Hello")
        turn2 = ConversationTurn(
            id="2",
            role="assistant",
            content="Hi!",
            metadata={"resident_session_id": "worker-session-1"},
            participant_id="hermes-1",
            participant_meta={"peer_id": "hermes-1", "persona": "Hermes"},
            thread_id="thread-1",
            visibility="internal",
        )

        test_broker._conversation_turns = [turn1, turn2]
        test_broker._save_conversation_history()

        # Verify file was created
        path = test_broker._conversation_history_path()
        assert path.exists()

        # Verify JSON content
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["turns"]) == 2
        assert data["turns"][0]["role"] == "user"
        assert data["turns"][1]["role"] == "assistant"

        # Clear and reload
        test_broker._conversation_turns = []
        test_broker._load_conversation_history()
        assert len(test_broker._conversation_turns) == 2
        assert test_broker._conversation_turns[0].content == "Hello"
        assert test_broker._conversation_turns[1].content == "Hi!"
        assert test_broker._conversation_turns[1].metadata == {
            "resident_session_id": "worker-session-1"
        }
        assert test_broker._conversation_turns[1].participant_id == "hermes-1"
        assert test_broker._conversation_turns[1].participant_meta == {
            "peer_id": "hermes-1",
            "persona": "Hermes",
        }
        assert test_broker._conversation_turns[1].thread_id == "thread-1"
        assert test_broker._conversation_turns[1].visibility == "internal"

    def test_load_nonexistent_file(self, test_broker):
        test_broker._load_conversation_history()
        assert test_broker._conversation_turns == []

    def test_load_corrupted_file(self, test_broker):
        path = test_broker._conversation_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json", encoding="utf-8")

        test_broker._load_conversation_history()
        assert test_broker._conversation_turns == []

    def test_append_turn_persists(self, test_broker):
        turn = ConversationTurn(id="1", role="user", content="Test")
        test_broker._append_turn(turn)

        assert len(test_broker._conversation_turns) == 1
        assert test_broker._conversation_history_path().exists()


class TestConversationTurnCapture:
    """Tests for capturing turns from CLI events."""

    @pytest.fixture
    def test_broker(self, tmp_path):
        settings = SkuldSettings(
            session={"id": "test-session", "workspace_dir": str(tmp_path)},
            transport="subprocess",
        )
        b = Broker(settings=settings)
        b._transport = MagicMock()
        b._transport.is_alive = True
        b._transport.capabilities = TransportCapabilities()
        return b

    @pytest.mark.asyncio
    async def test_user_turn_captured_on_message(self, test_broker):
        """User messages should create a user turn."""
        test_broker._transport.send_message = AsyncMock()

        await test_broker._dispatch_browser_message({"content": "Hello world"})

        assert len(test_broker._conversation_turns) == 1
        assert test_broker._conversation_turns[0].role == "user"
        assert test_broker._conversation_turns[0].content == "Hello world"

    @pytest.mark.asyncio
    async def test_assistant_turn_captured_on_result(self, test_broker):
        """Assistant turns should be captured when result event arrives."""
        # Simulate assistant event
        await test_broker._handle_cli_event({"type": "assistant", "content": []})

        # Simulate content delta
        await test_broker._handle_cli_event(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello "},
            }
        )
        await test_broker._handle_cli_event(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "there!"},
            }
        )

        # Simulate result event
        await test_broker._handle_cli_event(
            {
                "type": "result",
                "result": "",
                "modelUsage": {
                    "claude-sonnet-4-20250514": {
                        "inputTokens": 100,
                        "outputTokens": 50,
                        "costUSD": 0.001,
                    }
                },
            }
        )

        assert len(test_broker._conversation_turns) == 1
        turn = test_broker._conversation_turns[0]
        assert turn.role == "assistant"
        assert turn.content == "Hello there!"
        assert turn.metadata["cost"] == 0.001

    @pytest.mark.asyncio
    async def test_empty_result_not_captured(self, test_broker):
        """Result events with no content should not create a turn."""
        await test_broker._handle_cli_event(
            {
                "type": "result",
                "result": "",
                "modelUsage": {},
            }
        )

        assert len(test_broker._conversation_turns) == 0


class TestConversationHistoryEndpoint:
    """Tests for the /api/conversation/history endpoint."""

    @pytest.fixture(autouse=True)
    def _reset_broker(self):
        """Reset global broker's conversation state before each test."""
        broker._conversation_turns = []
        yield
        broker._conversation_turns = []

    def test_empty_history(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/conversation/history")
        assert response.status_code == 200
        data = response.json()
        assert data["turns"] == []
        assert "is_active" in data
        assert "last_activity" in data

    def test_history_with_turns(self):
        broker._conversation_turns = [
            ConversationTurn(
                id="1",
                role="user",
                content="Hello",
                created_at="2026-01-01T00:00:00+00:00",
            ),
            ConversationTurn(
                id="2",
                role="assistant",
                content="Hi!",
                created_at="2026-01-01T00:00:01+00:00",
                metadata={"model": "claude-sonnet-4-20250514"},
            ),
        ]

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/conversation/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data["turns"]) == 2
        assert data["turns"][0]["role"] == "user"
        assert data["turns"][0]["content"] == "Hello"
        assert data["turns"][1]["role"] == "assistant"
        assert data["turns"][1]["metadata"]["model"] == "claude-sonnet-4-20250514"
