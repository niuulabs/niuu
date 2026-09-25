"""Tests for PostgresPairingCodeRepository — asyncpg-backed pairing codes."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from niuu.adapters.postgres_pairing_codes import PostgresPairingCodeRepository

_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_EXPIRES = datetime(2024, 1, 1, 12, 10, 0, tzinfo=UTC)
_ID = UUID("00000000-0000-0000-0000-000000000001")


def _row(**kwargs) -> MagicMock:
    defaults = {
        "id": _ID,
        "code_hash": "hash-1",
        "created_by": "admin-1",
        "tenant_id": "tenant-a",
        "allow_plaintext": False,
        "allow_untrusted_node_auth": False,
        "expires_at": _EXPIRES,
        "created_at": _NOW,
        "consumed_at": None,
        "consumed_by_node_id": None,
    }
    defaults.update(kwargs)
    row = MagicMock()
    row.__getitem__ = lambda self, key: defaults[key]
    return row


def _repo() -> tuple[PostgresPairingCodeRepository, AsyncMock]:
    pool = AsyncMock()
    return PostgresPairingCodeRepository(pool=pool), pool


@pytest.mark.asyncio
async def test_create_inserts_and_returns_the_pairing_code() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = _row()

    code = await repo.create(
        code_hash="hash-1",
        created_by="admin-1",
        tenant_id="tenant-a",
        allow_plaintext=False,
        allow_untrusted_node_auth=False,
        expires_at=_EXPIRES,
    )

    assert code.id == str(_ID)
    assert code.consumed_at is None
    sql, *params = pool.fetchrow.call_args.args
    assert "INSERT INTO niuu_pairing_codes" in sql
    assert params == ["hash-1", "admin-1", "tenant-a", False, False, _EXPIRES]


@pytest.mark.asyncio
async def test_peek_returns_the_row_regardless_of_consumed_or_expired_state() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = _row(consumed_at=_NOW, allow_plaintext=True)

    code = await repo.peek("hash-1")

    assert code is not None
    assert code.consumed_at == _NOW
    assert code.allow_plaintext is True
    sql, code_hash = pool.fetchrow.call_args.args
    assert "SELECT" in sql
    assert "niuu_pairing_codes" in sql
    assert code_hash == "hash-1"


@pytest.mark.asyncio
async def test_peek_returns_none_when_unknown() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = None

    assert await repo.peek("hash-1") is None
