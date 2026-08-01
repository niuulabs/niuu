"""PostgreSQL advisory-lock adapter for credential refresh serialization."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from niuu.ports.credentials import CredentialRefreshLockPort

_LOCK_NAMESPACE = b"niuu:credential-refresh:v1\0"


class PostgresCredentialRefreshLock(CredentialRefreshLockPort):
    """Hold a transaction-scoped advisory lock for one credential identity."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def hold(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> AsyncIterator[None]:
        lock_id = _credential_lock_id(owner_type, owner_id, name)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock($1)", lock_id)
                yield


def _credential_lock_id(owner_type: str, owner_id: str, name: str) -> int:
    """Return a stable signed 64-bit PostgreSQL advisory-lock key."""
    identity = "\0".join((owner_type, owner_id, name)).encode()
    digest = hashlib.blake2b(_LOCK_NAMESPACE + identity, digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)
