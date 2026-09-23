"""Tests for PostgreSQL chronicle repository adapter."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_chronicles import PostgresChronicleRepository
from volundr.domain.models import Chronicle, ChronicleStatus


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool."""
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    return pool


@pytest.fixture
def repository(mock_pool) -> PostgresChronicleRepository:
    """Create a repository with mock pool."""
    return PostgresChronicleRepository(mock_pool)


@pytest.fixture
def sample_chronicle() -> Chronicle:
    """Create a sample chronicle for testing."""
    return Chronicle(
        id=uuid4(),
        session_id=uuid4(),
        status=ChronicleStatus.DRAFT,
        project="repo",
        repo="https://github.com/org/repo",
        branch="main",
        model="claude-sonnet-4-20250514",
        config_snapshot={
            "name": "test",
            "model": "claude-sonnet-4-20250514",
            "repo": "https://github.com/org/repo",
            "branch": "main",
        },
        summary="Test summary",
        key_changes=["file1.py", "file2.py"],
        unfinished_work="Fix tests",
        token_usage=1000,
        cost=Decimal("0.003"),
        duration_seconds=300,
        tags=["python", "testing"],
        parent_chronicle_id=None,
        owner_id="alice",
        tenant_id="t1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def sample_row(sample_chronicle: Chronicle) -> dict:
    """Create a sample database row matching sample_chronicle."""
    return {
        "id": sample_chronicle.id,
        "session_id": sample_chronicle.session_id,
        "status": sample_chronicle.status.value,
        "project": sample_chronicle.project,
        "repo": sample_chronicle.repo,
        "branch": sample_chronicle.branch,
        "model": sample_chronicle.model,
        "config_snapshot": sample_chronicle.config_snapshot,
        "summary": sample_chronicle.summary,
        "key_changes": sample_chronicle.key_changes,
        "unfinished_work": sample_chronicle.unfinished_work,
        "token_usage": sample_chronicle.token_usage,
        "cost": float(sample_chronicle.cost),
        "duration_seconds": sample_chronicle.duration_seconds,
        "tags": sample_chronicle.tags,
        "parent_chronicle_id": sample_chronicle.parent_chronicle_id,
        "owner_id": sample_chronicle.owner_id,
        "tenant_id": sample_chronicle.tenant_id,
        "created_at": sample_chronicle.created_at,
        "updated_at": sample_chronicle.updated_at,
    }


class TestPostgresChronicleRepositoryCreate:
    """Tests for create method."""

    async def test_create_executes_insert(
        self,
        repository: PostgresChronicleRepository,
        mock_pool,
        sample_chronicle: Chronicle,
    ):
        """Test that create executes INSERT statement."""
        await repository.create(sample_chronicle)

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args
        sql = call_args[0][0]
        assert "INSERT INTO chronicles" in sql
        assert call_args[0][1] == sample_chronicle.id
        assert call_args[0][2] == sample_chronicle.session_id
        assert call_args[0][3] == sample_chronicle.status.value
        assert "owner_id, tenant_id" in sql
        assert call_args[0][19:] == ("alice", "t1")

    async def test_create_returns_chronicle(
        self,
        repository: PostgresChronicleRepository,
        sample_chronicle: Chronicle,
    ):
        """Test that create returns the chronicle."""
        result = await repository.create(sample_chronicle)
        assert result == sample_chronicle


class TestPostgresChronicleRepositoryGet:
    """Tests for get method."""

    async def test_get_returns_chronicle_when_found(
        self,
        repository: PostgresChronicleRepository,
        mock_pool,
        sample_chronicle: Chronicle,
        sample_row,
    ):
        """Test that get returns chronicle when found."""
        mock_pool.fetchrow.return_value = sample_row

        result = await repository.get(sample_chronicle.id)

        assert result is not None
        assert result.id == sample_chronicle.id
        assert result.project == sample_chronicle.project
        assert result.model == sample_chronicle.model
        assert (result.owner_id, result.tenant_id) == ("alice", "t1")

    async def test_get_returns_none_when_not_found(
        self, repository: PostgresChronicleRepository, mock_pool
    ):
        """Test that get returns None when chronicle not found."""
        mock_pool.fetchrow.return_value = None

        result = await repository.get(uuid4())

        assert result is None

    async def test_get_executes_select_with_id(
        self, repository: PostgresChronicleRepository, mock_pool
    ):
        """Test that get executes SELECT with chronicle ID."""
        mock_pool.fetchrow.return_value = None
        chronicle_id = uuid4()

        await repository.get(chronicle_id)

        mock_pool.fetchrow.assert_called_once()
        call_args = mock_pool.fetchrow.call_args
        assert "SELECT * FROM chronicles WHERE id = $1" in call_args[0][0]
        assert call_args[0][1] == chronicle_id


class TestPostgresChronicleRepositoryGetBySession:
    """Tests for get_by_session method."""

    async def test_get_by_session_returns_chronicle(
        self,
        repository: PostgresChronicleRepository,
        mock_pool,
        sample_chronicle: Chronicle,
        sample_row,
    ):
        """Test that get_by_session returns chronicle with correct SQL."""
        mock_pool.fetchrow.return_value = sample_row

        result = await repository.get_by_session(sample_chronicle.session_id)

        assert result is not None
        assert result.id == sample_chronicle.id

        call_args = mock_pool.fetchrow.call_args
        sql = call_args[0][0]
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT 1" in sql

    async def test_get_by_session_returns_none(
        self, repository: PostgresChronicleRepository, mock_pool
    ):
        """Test that get_by_session returns None when no chronicle for session."""
        mock_pool.fetchrow.return_value = None

        result = await repository.get_by_session(uuid4())

        assert result is None


class TestPostgresChronicleRepositoryList:
    """Tests for list method."""

    async def test_list_no_filters(
        self, repository: PostgresChronicleRepository, mock_pool, sample_row
    ):
        """Test that list without filters uses base query."""
        mock_pool.fetch.return_value = [sample_row]

        result = await repository.list()

        assert len(result) == 1
        call_args = mock_pool.fetch.call_args
        sql = call_args[0][0]
        assert "WHERE" not in sql.split("ORDER BY")[0]

    async def test_list_with_project_filter(
        self, repository: PostgresChronicleRepository, mock_pool
    ):
        """Test that list with project filter adds WHERE clause."""
        mock_pool.fetch.return_value = []

        await repository.list(project="my-project")

        call_args = mock_pool.fetch.call_args
        sql = call_args[0][0]
        assert "WHERE" in sql
        assert "project = $1" in sql

    async def test_list_with_tags_filter(self, repository: PostgresChronicleRepository, mock_pool):
        """Test that list with tags filter uses @> operator."""
        mock_pool.fetch.return_value = []

        await repository.list(tags=["python", "testing"])

        call_args = mock_pool.fetch.call_args
        sql = call_args[0][0]
        assert "tags @>" in sql

    async def test_list_returns_empty(self, repository: PostgresChronicleRepository, mock_pool):
        """Test that list returns empty list when no results."""
        mock_pool.fetch.return_value = []

        result = await repository.list()

        assert result == []


class TestPostgresChronicleRepositoryGetChain:
    """Tests for get_chain method."""

    async def test_get_chain_executes_recursive_query(
        self,
        repository: PostgresChronicleRepository,
        mock_pool,
        sample_row,
    ):
        """Test that get_chain executes a recursive CTE query."""
        mock_pool.fetch.return_value = [sample_row]
        chronicle_id = sample_row["id"]

        result = await repository.get_chain(chronicle_id)

        assert len(result) == 1
        mock_pool.fetch.assert_called_once()
        call_args = mock_pool.fetch.call_args
        sql = call_args[0][0]
        assert "WITH RECURSIVE" in sql
        assert "ORDER BY created_at ASC" in sql

    async def test_get_chain_returns_empty_for_nonexistent(
        self,
        repository: PostgresChronicleRepository,
        mock_pool,
    ):
        """Test that get_chain returns empty list when chronicle not found."""
        mock_pool.fetch.return_value = []

        result = await repository.get_chain(uuid4())

        assert result == []


class TestPostgresChronicleRepositoryUpdate:
    """Tests for update method."""

    async def test_update_executes_update(
        self,
        repository: PostgresChronicleRepository,
        mock_pool,
        sample_chronicle: Chronicle,
    ):
        """Test that update executes UPDATE statement."""
        await repository.update(sample_chronicle)

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args
        sql = call_args[0][0]
        assert "UPDATE chronicles" in sql
        assert "WHERE id = $1" in sql
        # Attribution is the producing session's and never changes.
        assert "owner_id" not in sql and "tenant_id" not in sql

    async def test_update_returns_chronicle(
        self,
        repository: PostgresChronicleRepository,
        sample_chronicle: Chronicle,
    ):
        """Test that update returns the chronicle."""
        result = await repository.update(sample_chronicle)
        assert result == sample_chronicle


class TestPostgresChronicleRepositoryDelete:
    """Tests for delete method."""

    async def test_delete_returns_true_when_deleted(
        self, repository: PostgresChronicleRepository, mock_pool
    ):
        """Test that delete returns True when chronicle deleted."""
        mock_pool.execute.return_value = "DELETE 1"

        result = await repository.delete(uuid4())

        assert result is True

    async def test_delete_returns_false_when_not_found(
        self, repository: PostgresChronicleRepository, mock_pool
    ):
        """Test that delete returns False when chronicle not found."""
        mock_pool.execute.return_value = "DELETE 0"

        result = await repository.delete(uuid4())

        assert result is False


class TestRowToChronicle:
    """Tests for _row_to_chronicle helper."""

    async def test_converts_row_to_chronicle(
        self, repository: PostgresChronicleRepository, sample_row
    ):
        """Test that row is correctly converted to Chronicle."""
        result = repository._row_to_chronicle(sample_row)

        assert result.id == sample_row["id"]
        assert result.session_id == sample_row["session_id"]
        assert result.project == sample_row["project"]
        assert result.model == sample_row["model"]
        assert result.status == ChronicleStatus(sample_row["status"])
        assert result.summary == sample_row["summary"]
        assert result.key_changes == sample_row["key_changes"]
        assert result.tags == sample_row["tags"]

    async def test_handles_naive_datetime(
        self, repository: PostgresChronicleRepository, sample_row
    ):
        """Test that naive datetimes get UTC timezone."""
        sample_row["created_at"] = datetime(2024, 1, 1, 12, 0, 0)
        sample_row["updated_at"] = datetime(2024, 1, 1, 12, 0, 0)

        result = repository._row_to_chronicle(sample_row)

        assert result.created_at.tzinfo == UTC
        assert result.updated_at.tzinfo == UTC

    async def test_handles_null_cost(self, repository: PostgresChronicleRepository, sample_row):
        """Test that None cost is handled correctly."""
        sample_row["cost"] = None

        result = repository._row_to_chronicle(sample_row)

        assert result.cost is None
