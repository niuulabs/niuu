"""Tests for PostgresAuditAdapter — uses MagicMock to stub asyncpg Pool."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from bifrost.adapters.audit.postgres import PostgresAuditAdapter
from bifrost.ports.audit import AuditEvent
from tests.test_bifrost.conftest import make_pool_mock


def _event(**kwargs) -> AuditEvent:
    defaults = dict(
        request_id="req-1",
        agent_id="agent-1",
        tenant_id="tenant-1",
        session_id="sess",
        saga_id="saga",
        model="claude-sonnet-4-6",
        provider="anthropic",
        outcome="success",
        status_code=200,
        rule_name="",
        rule_action="",
        tags={},
        error_message="",
        latency_ms=42.0,
        timestamp=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return AuditEvent(**defaults)


_PATCH_PATH = "bifrost.adapters._pg_base.asyncpg.create_pool"


@pytest.fixture
async def pg_audit():
    """PostgresAuditAdapter with asyncpg.create_pool stubbed out."""
    pool, conn = make_pool_mock()
    with patch(_PATCH_PATH, new_callable=AsyncMock) as mock_cp:
        mock_cp.return_value = pool
        adapter = PostgresAuditAdapter(dsn="postgresql://fake/db")
        await adapter._get_pool()
        conn.execute.reset_mock()
        conn.fetch.reset_mock()
        conn.fetchrow.reset_mock()
        yield adapter, conn
    await adapter.close()


class TestPostgresAuditLog:
    async def test_log_calls_execute(self, pg_audit):
        adapter, conn = pg_audit
        await adapter.log(_event())
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO bifrost_audit" in sql

    async def test_log_passes_all_fields(self, pg_audit):
        adapter, conn = pg_audit
        e = _event(
            outcome="rejected",
            status_code=400,
            rule_name="block-images",
            rule_action="reject",
            tags={"key": "value"},
            error_message="content policy",
        )
        await adapter.log(e)
        _, *args = conn.execute.call_args[0]
        assert "rejected" in args
        assert 400 in args
        assert "block-images" in args

    async def test_log_uses_bifrost_audit_table(self, pg_audit):
        adapter, conn = pg_audit
        await adapter.log(_event())
        sql = conn.execute.call_args[0][0]
        assert "bifrost_audit" in sql

    async def test_log_serialises_tags_as_json(self, pg_audit):
        adapter, conn = pg_audit
        e = _event(tags={"env": "production", "tier": "premium"})
        await adapter.log(e)
        _, *args = conn.execute.call_args[0]
        # Tags are serialised as a JSON string parameter.
        json_args = [a for a in args if isinstance(a, str) and "production" in a]
        assert len(json_args) == 1

    async def test_log_empty_tags(self, pg_audit):
        adapter, conn = pg_audit
        await adapter.log(_event(tags={}))
        conn.execute.assert_called_once()


class TestPostgresAuditQuery:
    async def test_query_returns_empty_list(self, pg_audit):
        adapter, conn = pg_audit
        conn.fetch.return_value = []
        results = await adapter.query()
        assert results == []

    async def test_query_with_outcome_filter(self, pg_audit):
        adapter, conn = pg_audit
        conn.fetch.return_value = []
        await adapter.query(outcome="rejected")
        conn.fetch.assert_called_once()
        sql = conn.fetch.call_args[0][0]
        assert "outcome" in sql

    async def test_query_with_all_filters(self, pg_audit):
        adapter, conn = pg_audit
        conn.fetch.return_value = []
        await adapter.query(agent_id="a", tenant_id="t", model="m", outcome="success")
        sql = conn.fetch.call_args[0][0]
        assert "agent_id" in sql
        assert "tenant_id" in sql
        assert "model" in sql
        assert "outcome" in sql

    async def test_query_uses_bifrost_audit_table(self, pg_audit):
        adapter, conn = pg_audit
        conn.fetch.return_value = []
        await adapter.query()
        sql = conn.fetch.call_args[0][0]
        assert "bifrost_audit" in sql

    async def test_query_default_limit(self, pg_audit):
        adapter, conn = pg_audit
        conn.fetch.return_value = []
        await adapter.query()
        _sql, *params = conn.fetch.call_args[0]
        assert 1000 in params


class TestPostgresAuditClose:
    async def test_close_calls_pool_close(self):
        pool, conn = make_pool_mock()
        with patch(_PATCH_PATH, new_callable=AsyncMock) as mock_cp:
            mock_cp.return_value = pool
            adapter = PostgresAuditAdapter(dsn="postgresql://fake/db")
            await adapter._get_pool()
            await adapter.close()
        pool.close.assert_called_once()

    async def test_close_is_idempotent(self):
        pool, conn = make_pool_mock()
        with patch(_PATCH_PATH, new_callable=AsyncMock) as mock_cp:
            mock_cp.return_value = pool
            adapter = PostgresAuditAdapter(dsn="postgresql://fake/db")
            await adapter._get_pool()
            await adapter.close()
            await adapter.close()
        pool.close.assert_called_once()


class TestPostgresAuditDsnEnv:
    def test_dsn_env_fallback(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://env-host/db")
        adapter = PostgresAuditAdapter()
        assert adapter._dsn == "postgresql://env-host/db"

    def test_explicit_dsn_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://env-host/db")
        adapter = PostgresAuditAdapter(dsn="postgresql://explicit/db")
        assert adapter._dsn == "postgresql://explicit/db"

    def test_custom_dsn_env(self, monkeypatch):
        monkeypatch.setenv("MY_AUDIT_URL", "postgresql://audit/db")
        adapter = PostgresAuditAdapter(dsn_env="MY_AUDIT_URL")
        assert adapter._dsn == "postgresql://audit/db"


class TestPostgresAuditSchemaInit:
    async def test_schema_is_not_created_on_first_pool_acquire(self):
        pool, conn = make_pool_mock()
        with patch(_PATCH_PATH, new_callable=AsyncMock) as mock_cp:
            mock_cp.return_value = pool
            adapter = PostgresAuditAdapter(dsn="postgresql://fake/db")
            await adapter._get_pool()

        # Schema ownership lives in explicit service migrations, so first pool
        # acquisition should not emit runtime auto-DDL.
        conn.execute.assert_not_called()
