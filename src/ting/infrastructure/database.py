"""Database infrastructure for PostgreSQL."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg

from ting.config import DatabaseConfig


async def create_pool(config: DatabaseConfig) -> asyncpg.Pool:
    """Create an asyncpg connection pool."""
    return await asyncpg.create_pool(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.name,
        min_size=config.min_pool_size,
        max_size=config.max_pool_size,
    )


async def init_db(pool: asyncpg.Pool) -> None:
    """Initialize database schema.

    Note: Schema migrations are handled by the migrate init container
    in Kubernetes. This is a no-op placeholder for development.
    """


@asynccontextmanager
async def database_pool(config: DatabaseConfig) -> AsyncGenerator[asyncpg.Pool, None]:
    """Context manager for database pool lifecycle."""
    pool = await create_pool(config)
    try:
        await init_db(pool)
        yield pool
    finally:
        await pool.close()
