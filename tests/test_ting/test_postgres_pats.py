"""Tests for PostgresPATRepository with mocked asyncpg pool."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.domain.models import PersonalAccessToken


@pytest.fixture
def mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def repo(mock_pool: MagicMock) -> PostgresPATRepository:
    return PostgresPATRepository(mock_pool)


def _make_row(
    owner_id: str = "user-1",
    name: str = "my-token",
) -> dict:
    return {
        "id": uuid4(),
        "owner_id": owner_id,
        "name": name,
        "created_at": datetime.now(UTC),
        "last_used_at": None,
    }


class TestCreate:
    @pytest.mark.asyncio
    async def test_inserts_and_returns_pat(self, repo: PostgresPATRepository, mock_pool: MagicMock):
        row = _make_row()
        mock_pool.fetchrow = AsyncMock(return_value=row)

        pat = await repo.create("user-1", "my-token", "hash123")

        mock_pool.fetchrow.assert_called_once()
        assert mock_pool.fetchrow.call_args[0][1] == "user-1"
        assert mock_pool.fetchrow.call_args[0][2] == "my-token"
        assert mock_pool.fetchrow.call_args[0][3] == "hash123"
        assert isinstance(pat, PersonalAccessToken)
        assert pat.owner_id == "user-1"
        assert pat.name == "my-token"

    @pytest.mark.asyncio
    async def test_returns_correct_id(self, repo: PostgresPATRepository, mock_pool: MagicMock):
        row = _make_row()
        mock_pool.fetchrow = AsyncMock(return_value=row)

        pat = await repo.create("user-1", "my-token", "hash123")

        assert pat.id == row["id"]


class TestList:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self, repo: PostgresPATRepository):
        result = await repo.list("user-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_pats_for_owner(self, repo: PostgresPATRepository, mock_pool: MagicMock):
        rows = [_make_row(name="tok-1"), _make_row(name="tok-2")]
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await repo.list("user-1")

        assert len(result) == 2
        assert result[0].name == "tok-1"
        assert result[1].name == "tok-2"

    @pytest.mark.asyncio
    async def test_queries_with_owner_id(self, repo: PostgresPATRepository, mock_pool: MagicMock):
        await repo.list("user-42")

        mock_pool.fetch.assert_called_once()
        assert mock_pool.fetch.call_args[0][1] == "user-42"


class TestGet:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, repo: PostgresPATRepository):
        result = await repo.get(uuid4(), "user-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_pat_when_found(self, repo: PostgresPATRepository, mock_pool: MagicMock):
        row = _make_row()
        pat_id = row["id"]
        mock_pool.fetchrow = AsyncMock(return_value=row)

        result = await repo.get(pat_id, "user-1")

        assert result is not None
        assert result.id == pat_id

    @pytest.mark.asyncio
    async def test_scopes_query_to_owner(self, repo: PostgresPATRepository, mock_pool: MagicMock):
        pat_id = uuid4()
        await repo.get(pat_id, "user-1")

        mock_pool.fetchrow.assert_called_once()
        assert mock_pool.fetchrow.call_args[0][1] == pat_id
        assert mock_pool.fetchrow.call_args[0][2] == "user-1"


class TestDelete:
    @pytest.mark.asyncio
    async def test_returns_token_hash_when_deleted(
        self, repo: PostgresPATRepository, mock_pool: MagicMock
    ):
        mock_pool.fetchrow = AsyncMock(return_value={"token_hash": "abc123hash"})

        result = await repo.delete(uuid4(), "user-1")

        assert result == "abc123hash"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(
        self, repo: PostgresPATRepository, mock_pool: MagicMock
    ):
        mock_pool.fetchrow = AsyncMock(return_value=None)

        result = await repo.delete(uuid4(), "user-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_scopes_delete_to_owner(self, repo: PostgresPATRepository, mock_pool: MagicMock):
        pat_id = uuid4()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await repo.delete(pat_id, "user-1")

        mock_pool.fetchrow.assert_called_once()
        assert mock_pool.fetchrow.call_args[0][1] == pat_id
        assert mock_pool.fetchrow.call_args[0][2] == "user-1"


class TestExistsByHash:
    @pytest.mark.asyncio
    async def test_returns_true_when_found(self, repo: PostgresPATRepository, mock_pool: MagicMock):
        mock_pool.fetchrow.return_value = {"1": 1}

        result = await repo.exists_by_hash("abc123hash")

        assert result is True
        mock_pool.fetchrow.assert_called_once()
        assert mock_pool.fetchrow.call_args[0][1] == "abc123hash"

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(
        self,
        repo: PostgresPATRepository,
        mock_pool: MagicMock,
    ):
        mock_pool.fetchrow.return_value = None

        result = await repo.exists_by_hash("nonexistent")

        assert result is False


class TestTouchLastUsed:
    @pytest.mark.asyncio
    async def test_calls_execute_with_token_hash(
        self,
        repo: PostgresPATRepository,
        mock_pool: MagicMock,
    ):
        await repo.touch_last_used("abc123hash")

        mock_pool.execute.assert_called_once()
        assert mock_pool.execute.call_args[0][1] == "abc123hash"

    @pytest.mark.asyncio
    async def test_does_not_raise_on_missing_hash(
        self,
        repo: PostgresPATRepository,
        mock_pool: MagicMock,
    ):
        """touch_last_used is fire-and-forget; no error if hash not found."""
        mock_pool.execute = AsyncMock(return_value=None)

        await repo.touch_last_used("nonexistent-hash")

        mock_pool.execute.assert_called_once()


class TestRowToPat:
    def test_converts_row_to_domain_model(self):
        row = _make_row()
        pat = PostgresPATRepository._row_to_pat(row)

        assert isinstance(pat, PersonalAccessToken)
        assert pat.id == row["id"]
        assert pat.owner_id == row["owner_id"]
        assert pat.name == row["name"]
        assert pat.created_at == row["created_at"]
        assert pat.last_used_at is None

    def test_converts_row_with_last_used(self):
        row = _make_row()
        row["last_used_at"] = datetime.now(UTC)
        pat = PostgresPATRepository._row_to_pat(row)

        assert pat.last_used_at == row["last_used_at"]
