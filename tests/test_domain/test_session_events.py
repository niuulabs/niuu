"""Tests for SessionEvent and SessionEventType domain models."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from volundr.domain.models import SessionEvent, SessionEventType


class TestSessionEventType:
    """Tests for the SessionEventType enum."""

    def test_all_event_types_have_values(self):
        assert len(SessionEventType) == 15

    def test_event_types_are_lowercase(self):
        for et in SessionEventType:
            assert et.value == et.value.lower()

    def test_message_types(self):
        assert SessionEventType.MESSAGE_USER == "message_user"
        assert SessionEventType.MESSAGE_ASSISTANT == "message_assistant"

    def test_file_types(self):
        assert SessionEventType.FILE_CREATED == "file_created"
        assert SessionEventType.FILE_MODIFIED == "file_modified"
        assert SessionEventType.FILE_DELETED == "file_deleted"

    def test_git_types(self):
        assert SessionEventType.GIT_COMMIT == "git_commit"
        assert SessionEventType.GIT_PUSH == "git_push"
        assert SessionEventType.GIT_BRANCH == "git_branch"
        assert SessionEventType.GIT_CHECKOUT == "git_checkout"

    def test_lifecycle_types(self):
        assert SessionEventType.SESSION_START == "session_start"
        assert SessionEventType.SESSION_STOP == "session_stop"

    def test_from_string(self):
        assert SessionEventType("file_modified") == SessionEventType.FILE_MODIFIED
        assert SessionEventType("token_usage") == SessionEventType.TOKEN_USAGE


class TestSessionEvent:
    """Tests for the SessionEvent dataclass."""

    def test_create_minimal(self):
        event = SessionEvent(
            id=uuid4(),
            session_id=uuid4(),
            event_type=SessionEventType.MESSAGE_ASSISTANT,
            timestamp=datetime.now(UTC),
            data={},
            sequence=0,
        )
        assert event.tokens_in is None
        assert event.tokens_out is None
        assert event.cost is None
        assert event.duration_ms is None
        assert event.model is None

    def test_create_full(self):
        event_id = uuid4()
        session_id = uuid4()
        now = datetime.now(UTC)
        event = SessionEvent(
            id=event_id,
            session_id=session_id,
            event_type=SessionEventType.TOKEN_USAGE,
            timestamp=now,
            data={"provider": "cloud", "model": "sonnet", "tokens_in": 100, "tokens_out": 50},
            sequence=5,
            tokens_in=100,
            tokens_out=50,
            cost=Decimal("0.003"),
            duration_ms=500,
            model="claude-sonnet-4-20250514",
        )
        assert event.id == event_id
        assert event.session_id == session_id
        assert event.event_type == SessionEventType.TOKEN_USAGE
        assert event.tokens_in == 100
        assert event.tokens_out == 50
        assert event.cost == Decimal("0.003")
        assert event.duration_ms == 500
        assert event.model == "claude-sonnet-4-20250514"

    def test_frozen(self):
        event = SessionEvent(
            id=uuid4(),
            session_id=uuid4(),
            event_type=SessionEventType.ERROR,
            timestamp=datetime.now(UTC),
            data={"message": "test"},
            sequence=0,
        )
        try:
            event.sequence = 99  # type: ignore
            assert False, "Should have raised"
        except AttributeError:
            pass  # Expected: frozen dataclass should reject mutation
