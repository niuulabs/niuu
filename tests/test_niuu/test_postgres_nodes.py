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
async def test_create_inserts_and_returns_the_node() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = _row()

    node = await repo.create(
        node_id=str(_NODE_ID),
        name="spark-1",
        public_key="cHVibGljLWtleQ==",
        tenant_id="tenant-a",
        created_by="admin-1",
    )

    assert node.id == str(_NODE_ID)
    assert node.name == "spark-1"
    sql, *params = pool.fetchrow.call_args.args
    assert "INSERT INTO niuu_nodes" in sql
    assert params == [str(_NODE_ID), "spark-1", "cHVibGljLWtleQ==", "tenant-a", "admin-1"]


@pytest.mark.asyncio
async def test_get_returns_none_when_missing() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = None

    assert await repo.get(str(_NODE_ID)) is None


@pytest.mark.asyncio
async def test_get_maps_the_row() -> None:
    repo, pool = _repo()
    pool.fetchrow.return_value = _row(last_request_at=1700000000)

    node = await repo.get(str(_NODE_ID))

    assert node is not None
    assert node.last_request_at == 1700000000


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
async def test_record_request_persists_the_replay_watermark() -> None:
    repo, pool = _repo()

    await repo.record_request(str(_NODE_ID), timestamp=1700000000)

    sql, timestamp, node_id = pool.execute.call_args.args
    assert "last_request_at" in sql
    assert timestamp == 1700000000
    assert node_id == str(_NODE_ID)


@pytest.mark.asyncio
async def test_delete_removes_the_node() -> None:
    repo, pool = _repo()

    await repo.delete(str(_NODE_ID))

    sql, node_id = pool.execute.call_args.args
    assert "DELETE FROM niuu_nodes" in sql
    assert node_id == str(_NODE_ID)
