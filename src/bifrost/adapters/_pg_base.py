"""Base class for asyncpg-backed adapters.

Provides connection-pool lifecycle management with safe concurrent
initialisation via ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg


class PostgresBase:
    """Shared pool lifecycle for asyncpg adapters.

    Subclasses must set ``_create_table_sql`` and ``_create_indexes_sql``
    class attributes (or override ``_init_schema``).

    Args:
        dsn:      PostgreSQL connection string.  When blank, falls back
                  to the environment variable named by *dsn_env*.
        dsn_env:  Environment variable holding the DSN (default: ``DATABASE_URL``).
        min_size: Minimum connection pool size.
        max_size: Maximum connection pool size.
    """

    _create_table_sql: str = ""
    _create_indexes_sql: str = ""

    def __init__(
        self,
        dsn: str = "",
        dsn_env: str = "DATABASE_URL",
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        self._dsn = dsn or os.environ.get(dsn_env, "")
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                )
                await self._init_schema(self._pool)
        return self._pool

    async def _init_schema(self, pool: asyncpg.Pool) -> None:
        """Initialize adapter state after the pool is created.

        Schema ownership now lives in explicit service migrations instead of
        runtime auto-DDL on first connection.
        """
        _ = pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
