"""Tests for PostgreSQL session repository adapter."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.domain.models import GitSource, Session, SessionStatus


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool."""
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    return pool


@pytest.fixture
def repository(mock_pool) -> PostgresSessionRepository:
    """Create a repository with mock pool."""
    return PostgresSessionRepository(mock_pool)


@pytest.fixture
def sample_session() -> Session:
    """Create a sample session for testing."""
    return Session(
        id=uuid4(),
        name="Test Session",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
        status=SessionStatus.CREATED,
        chat_endpoint=None,
        code_endpoint=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def sample_row(sample_session: Session) -> dict:
    """Create a sample database row matching sample_session."""
    return {
        "id": sample_session.id,
        "name": sample_session.name,
        "model": sample_session.model,
        "repo": sample_session.repo,
        "branch": sample_session.branch,
        "status": sample_session.status.value,
        "chat_endpoint": sample_session.chat_endpoint,
        "code_endpoint": sample_session.code_endpoint,
        "created_at": sample_session.created_at,
        "updated_at": sample_session.updated_at,
        "last_active": sample_session.last_active,
        "message_count": sample_session.message_count,
        "tokens_used": sample_session.tokens_used,
        "pod_name": sample_session.pod_name,
        "error": sample_session.error,
    }


class TestPostgresSessionRepositoryCreate:
    """Tests for create method."""

    async def test_create_executes_insert(
        self, repository: PostgresSessionRepository, mock_pool, sample_session: Session
    ):
        """Test that create executes INSERT statement."""
        await repository.create(sample_session)

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args
        sql = call_args[0][0]
        assert "INSERT INTO sessions" in sql
        assert call_args[0][1] == sample_session.id
        assert call_args[0][2] == sample_session.name
        assert call_args[0][3] == sample_session.model

    async def test_create_returns_session(
        self, repository: PostgresSessionRepository, sample_session: Session
    ):
        """Test that create returns the session."""
        result = await repository.create(sample_session)
        assert result == sample_session


class TestPostgresSessionRepositoryGet:
    """Tests for get method."""

    async def test_get_returns_session_when_found(
        self, repository: PostgresSessionRepository, mock_pool, sample_session: Session, sample_row
    ):
        """Test that get returns session when found."""
        mock_pool.fetchrow.return_value = sample_row

        result = await repository.get(sample_session.id)

        assert result is not None
        assert result.id == sample_session.id
        assert result.name == sample_session.name
        assert result.model == sample_session.model

    async def test_get_returns_none_when_not_found(
        self, repository: PostgresSessionRepository, mock_pool
    ):
        """Test that get returns None when session not found."""
        mock_pool.fetchrow.return_value = None

        result = await repository.get(uuid4())

        assert result is None

    async def test_get_executes_select_with_id(
        self, repository: PostgresSessionRepository, mock_pool
    ):
        """Test that get executes SELECT with session ID."""
        mock_pool.fetchrow.return_value = None
        session_id = uuid4()

        await repository.get(session_id)

        mock_pool.fetchrow.assert_called_once()
        call_args = mock_pool.fetchrow.call_args
        assert "SELECT * FROM sessions WHERE id = $1" in call_args[0][0]
        assert call_args[0][1] == session_id


class TestPostgresSessionRepositoryGetMany:
    """Tests for get_many method."""

    async def test_get_many_returns_empty_for_empty_input(
        self, repository: PostgresSessionRepository, mock_pool
    ):
        result = await repository.get_many([])
        assert result == {}
        mock_pool.fetch.assert_not_called()

    async def test_get_many_returns_sessions_by_id(
        self, repository: PostgresSessionRepository, mock_pool, sample_row
    ):
        sid = sample_row["id"]
        mock_pool.fetch.return_value = [sample_row]

        result = await repository.get_many([sid])

        assert len(result) == 1
        assert sid in result
        assert result[sid].name == sample_row["name"]
        mock_pool.fetch.assert_called_once()
        call_args = mock_pool.fetch.call_args
        assert "ANY($1::uuid[])" in call_args[0][0]

    async def test_get_many_returns_subset_for_missing_ids(
        self, repository: PostgresSessionRepository, mock_pool, sample_row
    ):
        sid = sample_row["id"]
        missing_id = uuid4()
        mock_pool.fetch.return_value = [sample_row]

        result = await repository.get_many([sid, missing_id])

        assert len(result) == 1
        assert sid in result
        assert missing_id not in result


class TestPostgresSessionRepositoryList:
    """Tests for list method."""

    async def test_list_returns_all_sessions(
        self, repository: PostgresSessionRepository, mock_pool, sample_row
    ):
        """Test that list returns all sessions."""
        mock_pool.fetch.return_value = [sample_row, sample_row]

        result = await repository.list()

        assert len(result) == 2

    async def test_list_returns_empty_when_no_sessions(
        self, repository: PostgresSessionRepository, mock_pool
    ):
        """Test that list returns empty list when no sessions."""
        mock_pool.fetch.return_value = []

        result = await repository.list()

        assert result == []

    async def test_list_orders_by_created_at_desc(
        self, repository: PostgresSessionRepository, mock_pool
    ):
        """Test that list orders by created_at descending."""
        mock_pool.fetch.return_value = []

        await repository.list()

        call_args = mock_pool.fetch.call_args
        assert "ORDER BY created_at DESC" in call_args[0][0]


class TestPostgresSessionRepositoryUpdate:
    """Tests for update method."""

    async def test_update_executes_update(
        self, repository: PostgresSessionRepository, mock_pool, sample_session: Session
    ):
        """Test that update executes UPDATE statement."""
        await repository.update(sample_session)

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args
        sql = call_args[0][0]
        assert "UPDATE sessions" in sql
        assert "WHERE id = $1" in sql

    async def test_update_returns_session(
        self, repository: PostgresSessionRepository, sample_session: Session
    ):
        """Test that update returns the session."""
        result = await repository.update(sample_session)
        assert result == sample_session


class TestPostgresSessionRepositoryDelete:
    """Tests for delete method."""

    async def test_delete_returns_true_when_deleted(
        self, repository: PostgresSessionRepository, mock_pool
    ):
        """Test that delete returns True when session deleted."""
        mock_pool.execute.return_value = "DELETE 1"

        result = await repository.delete(uuid4())

        assert result is True

    async def test_delete_returns_false_when_not_found(
        self, repository: PostgresSessionRepository, mock_pool
    ):
        """Test that delete returns False when session not found."""
        mock_pool.execute.return_value = "DELETE 0"

        result = await repository.delete(uuid4())

        assert result is False

    async def test_delete_executes_delete_with_id(
        self, repository: PostgresSessionRepository, mock_pool
    ):
        """Test that delete executes DELETE with session ID."""
        mock_pool.execute.return_value = "DELETE 0"
        session_id = uuid4()

        await repository.delete(session_id)

        call_args = mock_pool.execute.call_args
        assert "DELETE FROM sessions WHERE id = $1" in call_args[0][0]
        assert call_args[0][1] == session_id


class TestRowToSession:
    """Tests for _row_to_session helper."""

    async def test_converts_row_to_session(self, repository: PostgresSessionRepository, sample_row):
        """Test that row is correctly converted to Session."""
        result = repository._row_to_session(sample_row)

        assert result.id == sample_row["id"]
        assert result.name == sample_row["name"]
        assert result.model == sample_row["model"]
        assert result.status == SessionStatus(sample_row["status"])
        assert result.chat_endpoint == sample_row["chat_endpoint"]
        assert result.code_endpoint == sample_row["code_endpoint"]

    async def test_handles_naive_datetime(self, repository: PostgresSessionRepository, sample_row):
        """Test that naive datetimes get UTC timezone."""
        sample_row["created_at"] = datetime(2024, 1, 1, 12, 0, 0)
        sample_row["updated_at"] = datetime(2024, 1, 1, 12, 0, 0)
        sample_row["last_active"] = datetime(2024, 1, 1, 12, 0, 0)

        result = repository._row_to_session(sample_row)

        assert result.created_at.tzinfo == UTC
        assert result.updated_at.tzinfo == UTC
        assert result.last_active.tzinfo == UTC

    async def test_handles_running_status_with_endpoints(
        self, repository: PostgresSessionRepository, sample_row
    ):
        """Test conversion of running session with endpoints."""
        sample_row["status"] = "running"
        sample_row["chat_endpoint"] = "wss://chat.example.com"
        sample_row["code_endpoint"] = "https://code.example.com"

        result = repository._row_to_session(sample_row)

        assert result.status == SessionStatus.RUNNING
        assert result.chat_endpoint == "wss://chat.example.com"
        assert result.code_endpoint == "https://code.example.com"
