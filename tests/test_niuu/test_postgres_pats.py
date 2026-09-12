"""Tests for PostgresPATRepository — asyncpg-backed PAT storage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from niuu.adapters.postgres_pats import PostgresPATRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_PAT_ID = UUID("00000000-0000-0000-0000-000000000001")
_OWNER = "user-abc"
_NAME = "my-pat"
_HASH = "sha256:abc123"


def _make_row(**kwargs) -> MagicMock:
    """Build a fake asyncpg Record-like object."""
    defaults = {
        "id": _PAT_ID,
        "owner_id": _OWNER,
        "name": _NAME,
        "created_at": _NOW,
        "last_used_at": None,
        "tenant_id": "acme",
        "scopes": None,
        "expires_at": None,
        "token_hash": _HASH,
    }
    defaults.update(kwargs)
    row = MagicMock()
    row.__getitem__ = lambda self, key: defaults[key]
    return row


def _make_repo() -> tuple[PostgresPATRepository, AsyncMock]:
    pool = AsyncMock()
    repo = PostgresPATRepository(pool=pool)
    return repo, pool


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_pat():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = _make_row()

    pat = await repo.create(owner_id=_OWNER, name=_NAME, token_hash=_HASH)

    assert pat.id == _PAT_ID
    assert pat.owner_id == _OWNER
    assert pat.name == _NAME
    assert pat.created_at == _NOW
    assert pat.last_used_at is None
    pool.fetchrow.assert_awaited_once()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_pats():
    repo, pool = _make_repo()
    pool.fetch.return_value = [_make_row(), _make_row(name="other-pat")]

    pats = await repo.list(owner_id=_OWNER)

    assert len(pats) == 2
    pool.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_returns_empty_when_none():
    repo, pool = _make_repo()
    pool.fetch.return_value = []

    pats = await repo.list(owner_id=_OWNER)

    assert pats == []


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_pat():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = _make_row()

    pat = await repo.get(pat_id=_PAT_ID, owner_id=_OWNER)

    assert pat is not None
    assert pat.id == _PAT_ID


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = None

    pat = await repo.get(pat_id=_PAT_ID, owner_id=_OWNER)

    assert pat is None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_returns_token_hash():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = _make_row()

    result = await repo.delete(pat_id=_PAT_ID, owner_id=_OWNER)

    assert result == _HASH


@pytest.mark.asyncio
async def test_delete_returns_none_when_not_found():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = None

    result = await repo.delete(pat_id=_PAT_ID, owner_id=_OWNER)

    assert result is None


# ---------------------------------------------------------------------------
# exists_by_hash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exists_by_hash_true():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = _make_row()

    exists = await repo.exists_by_hash(token_hash=_HASH)

    assert exists is True


@pytest.mark.asyncio
async def test_exists_by_hash_false():
    repo, pool = _make_repo()
    pool.fetchrow.return_value = None

    exists = await repo.exists_by_hash(token_hash="nonexistent")

    assert exists is False


# ---------------------------------------------------------------------------
# touch_last_used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_touch_last_used_calls_execute():
    repo, pool = _make_repo()
    pool.execute.return_value = None

    await repo.touch_last_used(token_hash=_HASH)

    pool.execute.assert_awaited_once()
