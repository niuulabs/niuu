"""Tests for the resident_runtimes-backed OwnerTenantResolverPort adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.resident_tenant_resolver import (
    LazyPostgresResidentTenantResolver,
    PostgresResidentTenantResolver,
)


class TestPostgresResidentTenantResolver:
    async def test_returns_the_tenant_id_for_a_known_runtime(self) -> None:
        pool = AsyncMock()
        runtime_id = uuid4()
        pool.fetchrow.return_value = {"tenant_id": "tenant-a"}
        resolver = PostgresResidentTenantResolver(pool)

        result = await resolver.tenant_id_for_owner(str(runtime_id))

        assert result == "tenant-a"
        pool.fetchrow.assert_awaited_once_with(
            "SELECT tenant_id FROM resident_runtimes WHERE id = $1", runtime_id
        )

    async def test_returns_none_for_an_unknown_runtime(self) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = None
        resolver = PostgresResidentTenantResolver(pool)

        result = await resolver.tenant_id_for_owner(str(uuid4()))

        assert result is None

    async def test_returns_none_for_a_non_uuid_owner_id_without_querying(self) -> None:
        """owner_id here is always a resident_runtimes.id (the
        owner_id_claim_pattern extracts nothing else) — a malformed value
        must not reach asyncpg as a mistyped query parameter."""
        pool = AsyncMock()
        resolver = PostgresResidentTenantResolver(pool)

        result = await resolver.tenant_id_for_owner("not-a-uuid")

        assert result is None
        pool.fetchrow.assert_not_awaited()


class TestLazyPostgresResidentTenantResolver:
    def test_empty_dsn_raises_immediately(self) -> None:
        with pytest.raises(ValueError, match="non-empty dsn"):
            LazyPostgresResidentTenantResolver("")

    def test_whitespace_only_dsn_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty dsn"):
            LazyPostgresResidentTenantResolver("   ")

    async def test_resolves_via_a_lazily_connected_pool(self, monkeypatch) -> None:
        pool = AsyncMock()
        runtime_id = uuid4()
        pool.fetchrow.return_value = {"tenant_id": "tenant-z"}

        async def _fake_create_pool(*, dsn, min_size, max_size):
            del dsn, min_size, max_size
            return pool

        import asyncpg

        monkeypatch.setattr(asyncpg, "create_pool", _fake_create_pool)
        resolver = LazyPostgresResidentTenantResolver("postgresql://example/db")

        result = await resolver.tenant_id_for_owner(str(runtime_id))

        assert result == "tenant-z"

    async def test_pool_is_connected_only_once_across_calls(self, monkeypatch) -> None:
        pool = AsyncMock()
        pool.fetchrow.return_value = None
        calls = {"n": 0}

        async def _fake_create_pool(*, dsn, min_size, max_size):
            del dsn, min_size, max_size
            calls["n"] += 1
            return pool

        import asyncpg

        monkeypatch.setattr(asyncpg, "create_pool", _fake_create_pool)
        resolver = LazyPostgresResidentTenantResolver("postgresql://example/db")

        await resolver.tenant_id_for_owner(str(uuid4()))
        await resolver.tenant_id_for_owner(str(uuid4()))

        assert calls["n"] == 1
