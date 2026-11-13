"""PostgresSessionRepository.list_by_workflow_runs: SQL exercised against a mocked pool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from volundr.adapters.outbound.postgres import PostgresSessionRepository


async def test_no_runs_asks_nothing() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock()

    assert await PostgresSessionRepository(pool).list_by_workflow_runs([]) == []

    pool.fetch.assert_not_awaited()


async def test_runs_are_matched_on_the_launch_config_oldest_first() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])

    assert await PostgresSessionRepository(pool).list_by_workflow_runs(["run-1", "run-2"]) == []

    sql, *params = pool.fetch.call_args.args
    assert "(workload_config->>'workflow_run_id') = ANY($1::text[])" in sql
    assert "ORDER BY created_at ASC" in sql
    assert params == [["run-1", "run-2"]]
