"""TransactionalPool — wraps a single asyncpg connection with an open transaction.

Repository adapters use ``pool.acquire()`` internally.  This wrapper makes
``acquire()`` return the *same* connection (inside a BEGIN), so every
operation in a test shares one transaction that is rolled back afterward.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg


class TransactionalPool:
    """Drop-in replacement for ``asyncpg.Pool`` scoped to a single transaction.

    * ``acquire()`` yields the wrapped connection (no real pool checkout).
    * ``execute / fetch / fetchrow / fetchval`` delegate directly.
    * ``close()`` is a no-op — the real pool is managed by the session fixture.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # -- pool.acquire() compat ------------------------------------------------

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        """Yield the underlying connection without actually checking one out."""
        yield self._conn

    # -- direct query delegation -----------------------------------------------

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        return await self._conn.execute(query, *args, timeout=timeout)

    async def executemany(self, query: str, args: Any, timeout: float | None = None) -> None:
        await self._conn.executemany(query, args, timeout=timeout)

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[Any]:
        return await self._conn.fetch(query, *args, timeout=timeout)

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> Any:
        return await self._conn.fetchrow(query, *args, timeout=timeout)

    async def fetchval(self, query: str, *args: Any, timeout: float | None = None) -> Any:
        return await self._conn.fetchval(query, *args, timeout=timeout)

    # -- lifecycle no-ops ------------------------------------------------------

    async def close(self) -> None:
        """No-op — the session-scoped pool manages its own lifecycle."""
