"""Tests for PostgreSQL credential refresh locking."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from niuu.adapters.postgres_credential_refresh_lock import (
    PostgresCredentialRefreshLock,
    _credential_lock_id,
)


async def test_holds_transaction_scoped_advisory_lock() -> None:
    connection = MagicMock()
    connection.execute = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock()
    transaction.__aexit__ = AsyncMock()
    connection.transaction.return_value = transaction

    @asynccontextmanager
    async def acquire():
        yield connection

    pool = MagicMock()
    pool.acquire = acquire
    lock = PostgresCredentialRefreshLock(pool)

    entered = False
    async with lock.hold("user", "user-1", "codex-main"):
        entered = True

    assert entered is True
    connection.execute.assert_awaited_once_with(
        "SELECT pg_advisory_xact_lock($1)",
        _credential_lock_id("user", "user-1", "codex-main"),
    )
    transaction.__aenter__.assert_awaited_once()
    transaction.__aexit__.assert_awaited_once()


def test_lock_identity_is_stable_and_owner_scoped() -> None:
    first = _credential_lock_id("user", "user-1", "codex-main")

    assert first == _credential_lock_id("user", "user-1", "codex-main")
    assert first != _credential_lock_id("user", "user-2", "codex-main")
    assert first != _credential_lock_id("user", "user-1", "codex-secondary")
    assert -(2**63) <= first < 2**63
