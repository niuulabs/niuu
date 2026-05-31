"""Smoke test: verify the integration test infrastructure works."""

import pytest


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_database_connection(db_pool):
    """Verify we can connect and query the test database."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 AS ok")
        assert row["ok"] == 1
