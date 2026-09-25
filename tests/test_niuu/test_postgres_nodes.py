"""Tests for PostgresNodeRepository — asyncpg-backed Guild node registry."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from niuu.adapters.postgres_nodes import PostgresNodeRepository

_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_NODE_ID = UUID("00000000-0000-0000-0000-000000000001")


def _row(**kwargs) -> MagicMock:
    defaults = {
        "id": _NODE_ID,
        "name": "spark-1",
        "public_key": "cHVibGljLWtleQ==",
        "tenant_id": "tenant-a",
        "created_by": "admin-1",
        "allow_plaintext": False,
        "created_at": _NOW,
        "last_seen_at": None,
        "last_request_at": None,
    }
    defaults.update(kwargs)
    row = MagicMock()
    row.__getitem__ = lambda self, key: defaults[key]
    return row


def _repo() -> tuple[PostgresNodeRepository, AsyncMock]:
    pool = AsyncMock()
    return PostgresNodeRepository(pool=pool), pool


@pytest.mark.asyncio
async def test_get_returns_none_when_missing() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = None

    assert await repo.get(str(_NODE_ID)) is None


@pytest.mark.asyncio
async def test_get_maps_the_row() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = _row(last_request_at=1700000000123, allow_plaintext=True)

    node = await repo.get(str(_NODE_ID))

    assert node is not None
    assert node.last_request_at == 1700000000123
    assert node.allow_plaintext is True


@pytest.mark.asyncio
async def test_list_for_tenant_scopes_the_query() -> None:
    repo, pool = _repo()
    pool.fetch.return_value = [_row()]

    nodes = await repo.list_for_tenant("tenant-a")

    assert len(nodes) == 1
    sql, tenant_id = pool.fetch.call_args.args
    assert "WHERE tenant_id = $1" in sql
    assert tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_touch_heartbeat_updates_last_seen_at() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = _row(last_seen_at=_NOW)

    node = await repo.touch_heartbeat(str(_NODE_ID))

    assert node is not None
    assert node.last_seen_at == _NOW
    sql, node_id = pool.fetchrow.call_args.args
    assert "UPDATE niuu_nodes" in sql
    assert "last_seen_at = NOW()" in sql
    assert node_id == str(_NODE_ID)


@pytest.mark.asyncio
async def test_touch_heartbeat_returns_none_when_missing() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = None

    assert await repo.touch_heartbeat(str(_NODE_ID)) is None


@pytest.mark.asyncio
async def test_try_advance_watermark_issues_the_atomic_conditional_update() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = {"id": _NODE_ID}

    advanced = await repo.try_advance_watermark(str(_NODE_ID), timestamp_ms=1700000000123)

    assert advanced is True
    sql, node_id, timestamp_ms = pool.fetchrow.call_args.args
    assert "UPDATE niuu_nodes" in sql
    assert "SET last_request_at = $2" in sql
    assert "last_request_at IS NULL OR last_request_at < $2" in sql
    assert node_id == str(_NODE_ID)
    assert timestamp_ms == 1700000000123


@pytest.mark.asyncio
async def test_try_advance_watermark_returns_false_when_no_row_matched() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = None

    advanced = await repo.try_advance_watermark(str(_NODE_ID), timestamp_ms=1700000000123)

    assert advanced is False


@pytest.mark.asyncio
async def test_delete_removes_the_node() -> None:
    repo, pool = _repo()

    await repo.delete(str(_NODE_ID))

    sql, node_id = pool.execute.call_args.args
    assert "DELETE FROM niuu_nodes" in sql
    assert node_id == str(_NODE_ID)
