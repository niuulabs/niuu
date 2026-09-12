"""Tests for provisioning per-service databases on an external PostgreSQL server."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from niuu.service_databases import ensure_databases


@pytest.mark.asyncio
async def test_creates_only_missing_databases() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=[1, None, None])
    connect = AsyncMock(return_value=conn)

    with patch("niuu.service_databases.asyncpg.connect", connect):
        created = await ensure_databases(
            host="db",
            port=5432,
            user="niuu",
            password="pw",
            names=["volundr", "ting", "mimir"],
        )

    assert created == ("ting", "mimir")
    connect.assert_awaited_once_with(
        host="db", port=5432, user="niuu", password="pw", database="postgres"
    )
    executed = [call.args[0] for call in conn.execute.await_args_list]
    assert executed == ['CREATE DATABASE "ting"', 'CREATE DATABASE "mimir"']
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejects_invalid_names_before_creating() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)

    with (
        patch("niuu.service_databases.asyncpg.connect", AsyncMock(return_value=conn)),
        pytest.raises(ValueError, match="Invalid PostgreSQL database name"),
    ):
        await ensure_databases(
            host="db", port=5432, user="niuu", password="pw", names=["ok_name", "Bad;Name"]
        )

    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_failure_propagates() -> None:
    with (
        patch(
            "niuu.service_databases.asyncpg.connect",
            AsyncMock(side_effect=OSError("connection refused")),
        ),
        pytest.raises(OSError, match="connection refused"),
    ):
        await ensure_databases(host="db", port=5432, user="niuu", password="pw", names=["x"])
