"""Shared, DSN-keyed lazy asyncpg pool for Ravn's auxiliary Postgres stores.

Several dynamically-configured adapters (trigger store, budget ledger, …) may
be pointed at the same Postgres database. Each independently opening its own
pool would double a small pool's connection footprint for no reason — see
``ravn.adapters._pool_sizing`` for why that already mattered once. Adapters
that connect lazily ask this module for the pool holder for their DSN; the
same DSN always resolves to the same holder, and the same underlying pool.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ravn.adapters._pool_sizing import AUX_POOL_MAX_SIZE, AUX_POOL_MIN_SIZE

_pool_cache: dict[str, _LazyAsyncpgPool] = {}


class _LazyAsyncpgPool:
    """Connect to Postgres on first use. Not constructed directly — use
    :func:`lazy_pool_for_dsn`, which shares one instance per DSN."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any | None = None
        self._lock = asyncio.Lock()

    async def resolve(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                import asyncpg  # noqa: PLC0415

                self._pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=AUX_POOL_MIN_SIZE,
                    max_size=AUX_POOL_MAX_SIZE,
                )
        return self._pool

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def lazy_pool_for_dsn(dsn: str) -> _LazyAsyncpgPool:
    """Return the shared lazy pool holder for *dsn*, creating it on first ask."""
    holder = _pool_cache.get(dsn)
    if holder is None:
        holder = _LazyAsyncpgPool(dsn)
        _pool_cache[dsn] = holder
    return holder


async def aclose_all_pools() -> None:
    """Close every pool this process opened, exactly once each."""
    holders = list(_pool_cache.values())
    _pool_cache.clear()
    for holder in holders:
        await holder.aclose()
