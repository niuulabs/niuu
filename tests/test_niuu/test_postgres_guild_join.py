"""Tests for PostgresGuildJoinRepository — the atomic join transaction.

SQL adapter unit tests; real cross-statement transaction/rollback behavior
against a live database is exercised in CI integration tests (see
``.claude/rules/database.md`` — no Docker in unit tests).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import asyncpg
import pytest

from niuu.adapters.postgres_guild_join import PostgresGuildJoinRepository
from niuu.domain.models import InstanceKind, InstanceVisibility
from niuu.ports.guild_join_repository import InstanceWrite, NodeConflictError

_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_CODE_ID = UUID("00000000-0000-0000-0000-000000000001")
_NODE_ID = UUID("00000000-0000-0000-0000-000000000002")


def _row(mapping: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: mapping[key]
    return row


def _code_row(**overrides) -> MagicMock:
    mapping = {
        "id": _CODE_ID,
        "code_hash": "hash-1",
        "created_by": "admin-1",
        "tenant_id": "tenant-a",
        "allow_plaintext": False,
        "allow_untrusted_node_auth": False,
        "expires_at": _NOW,
        "created_at": _NOW,
    }
    mapping.update(overrides)
    return _row(mapping)


def _node_row(**overrides) -> MagicMock:
    mapping = {
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
    mapping.update(overrides)
    return _row(mapping)


@pytest.fixture
def setup():
    conn = AsyncMock()
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return PostgresGuildJoinRepository(pool), pool, conn


@pytest.mark.asyncio
async def test_returns_none_when_the_code_cannot_be_consumed(setup) -> None:
    repo, _pool, conn = setup
    conn.fetchrow.return_value = None

    result = await repo.consume_and_register(
        code_hash="hash-1", node_name="spark-1", public_key="key", instances=[]
    )

    assert result is None
    # Only the code UPDATE ran — no node or instance writes were attempted.
    assert conn.fetchrow.await_count == 1
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_consume_and_register_writes_node_and_instances_in_one_pass(setup) -> None:
    repo, _pool, conn = setup

    def _fetchrow(sql, *params):
        if "UPDATE niuu_pairing_codes" in sql:
            return _code_row()
        # INSERT INTO niuu_nodes — echo back the generated node_id (params[0])
        # the way RETURNING would on a real connection.
        return _node_row(id=UUID(params[0]))

    conn.fetchrow.side_effect = _fetchrow

    result = await repo.consume_and_register(
        code_hash="hash-1",
        node_name="spark-1",
        public_key="cHVibGljLWtleQ==",
        instances=[
            InstanceWrite(
                kind=InstanceKind.VOLUNDR,
                slug="node-abc-volundr",
                name="spark-1 (volundr)",
                base_url="https://spark-1.example.com",
                visibility=InstanceVisibility.TENANT,
                tenant_id="tenant-a",
                config={},
            )
        ],
    )

    assert result is not None
    code, node, instances = result
    # The adapter generates its own node_id and passes it as the INSERT
    # parameter; this mock's canned RETURNING row doesn't echo that value
    # back (a real database would), so assert internal consistency instead
    # of a specific id.
    assert code.consumed_by_node_id == node.id
    assert len(instances) == 1
    assert instances[0].node_id == node.id
    assert instances[0].base_url == "https://spark-1.example.com"

    insert_calls = [
        c for c in conn.execute.call_args_list if "INSERT INTO niuu_instances" in c.args[0]
    ]
    assert len(insert_calls) == 1
    consumed_by_update = [
        c for c in conn.execute.call_args_list if "consumed_by_node_id" in c.args[0]
    ]
    assert len(consumed_by_update) == 1


@pytest.mark.asyncio
async def test_a_name_or_key_conflict_raises_without_writing_instances(setup) -> None:
    repo, _pool, conn = setup
    conn.fetchrow.side_effect = [_code_row(), asyncpg.UniqueViolationError()]

    with pytest.raises(NodeConflictError):
        await repo.consume_and_register(
            code_hash="hash-1",
            node_name="spark-1",
            public_key="cHVibGljLWtleQ==",
            instances=[
                InstanceWrite(
                    kind=InstanceKind.VOLUNDR,
                    slug="node-abc-volundr",
                    name="spark-1 (volundr)",
                    base_url="https://spark-1.example.com",
                    visibility=InstanceVisibility.TENANT,
                    tenant_id="tenant-a",
                    config={},
                )
            ],
        )

    # No instance INSERT and no consumed_by_node_id UPDATE ever ran — the
    # exception propagates out of the `async with conn.transaction()` block
    # before either, which is what triggers the real driver's ROLLBACK.
    assert conn.execute.await_count == 0
