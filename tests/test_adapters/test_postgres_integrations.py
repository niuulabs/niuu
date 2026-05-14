"""Tests for PostgreSQL integration connection repository."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from volundr.adapters.outbound.postgres_integrations import (
    PostgresIntegrationRepository,
)
from volundr.domain.models import IntegrationConnection, IntegrationType


def _mock_row(**overrides):
    defaults = {
        "id": "conn-001",
        "owner_id": "user-1",
        "integration_type": "issue_tracker",
        "adapter": "volundr.adapters.linear.LinearProvider",
        "credential_name": "linear-token",
        "config": json.dumps({"team_id": "TEAM1"}),
        "enabled": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "slug": "linear",
    }
    defaults.update(overrides)
    row = MagicMock()
    row.__getitem__ = lambda self, key: defaults[key]
    row.get = lambda key, default=None: defaults.get(key, default)
    return row


def _make_repo(pool=None):
    if pool is None:
        pool = AsyncMock()
    return PostgresIntegrationRepository(pool), pool


class TestListConnections:
    async def test_list_no_filter(self):
        repo, pool = _make_repo()
        pool.fetch.return_value = [_mock_row(), _mock_row(id="conn-002")]

        result = await repo.list_connections("user-1")

        assert len(result) == 2
        pool.fetch.assert_called_once()
        call_sql = pool.fetch.call_args[0][0]
        assert "owner_id = $1" in call_sql
        assert pool.fetch.call_args[0][1] == "user-1"

    async def test_list_with_type_filter(self):
        repo, pool = _make_repo()
        pool.fetch.return_value = [_mock_row()]

        result = await repo.list_connections(
            "user-1",
            integration_type=IntegrationType.ISSUE_TRACKER,
        )

        assert len(result) == 1
        call_sql = pool.fetch.call_args[0][0]
        assert "integration_type = $2" in call_sql
        assert pool.fetch.call_args[0][2] == "issue_tracker"

    async def test_list_returns_empty(self):
        repo, pool = _make_repo()
        pool.fetch.return_value = []

        result = await repo.list_connections("user-none")

        assert result == []


class TestGetConnection:
    async def test_get_existing(self):
        repo, pool = _make_repo()
        pool.fetchrow.return_value = _mock_row(id="conn-123")

        result = await repo.get_connection("conn-123")

        assert result is not None
        assert isinstance(result, IntegrationConnection)
        assert result.id == "conn-123"
        assert result.integration_type == IntegrationType.ISSUE_TRACKER

    async def test_get_missing(self):
        repo, pool = _make_repo()
        pool.fetchrow.return_value = None

        result = await repo.get_connection("nonexistent")

        assert result is None


class TestSaveConnection:
    async def test_save_inserts(self):
        repo, pool = _make_repo()
        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id="conn-new",
            owner_id="user-1",
            integration_type=IntegrationType.SOURCE_CONTROL,
            adapter="volundr.adapters.github.GitHubProvider",
            credential_name="gh-token",
            config={"org": "myorg"},
            enabled=True,
            created_at=now,
            updated_at=now,
            slug="github",
        )

        result = await repo.save_connection(connection)

        assert result is connection
        pool.execute.assert_called_once()
        call_sql = pool.execute.call_args[0][0]
        assert "INSERT INTO integration_connections" in call_sql
        assert "ON CONFLICT (id) DO UPDATE" in call_sql
        assert "adapter = EXCLUDED.adapter" in call_sql
        assert "integration_type = EXCLUDED.integration_type" in call_sql

    async def test_save_passes_correct_params(self):
        repo, pool = _make_repo()
        now = datetime.now(UTC)
        connection = IntegrationConnection(
            id="conn-upsert",
            owner_id="user-2",
            integration_type=IntegrationType.MESSAGING,
            adapter="volundr.adapters.slack.SlackProvider",
            credential_name="slack-token",
            config={"channel": "#general"},
            enabled=False,
            created_at=now,
            updated_at=now,
            slug="slack",
        )

        await repo.save_connection(connection)

        args = pool.execute.call_args[0]
        assert args[1] == "conn-upsert"
        assert args[2] == "user-2"
        assert args[3] == "messaging"
        assert args[4] == "volundr.adapters.slack.SlackProvider"
        assert args[5] == "slack-token"
        assert json.loads(args[6]) == {"channel": "#general"}
        assert args[7] is False
        assert args[8] == "slack"


class TestDeleteConnection:
    async def test_delete_calls_execute(self):
        repo, pool = _make_repo()

        await repo.delete_connection("conn-del")

        pool.execute.assert_called_once()
        call_sql = pool.execute.call_args[0][0]
        assert "DELETE FROM integration_connections" in call_sql
        assert pool.execute.call_args[0][1] == "conn-del"


class TestRowToConnection:
    def test_converts_string_config(self):
        row = _mock_row(config=json.dumps({"key": "value"}))
        result = PostgresIntegrationRepository._row_to_connection(row)

        assert isinstance(result, IntegrationConnection)
        assert result.config == {"key": "value"}

    def test_converts_dict_config(self):
        row = _mock_row(config={"key": "value"})
        result = PostgresIntegrationRepository._row_to_connection(row)

        assert result.config == {"key": "value"}

    def test_handles_none_config(self):
        row = _mock_row(config=None)
        result = PostgresIntegrationRepository._row_to_connection(row)

        assert result.config == {}

    def test_maps_integration_type(self):
        row = _mock_row(integration_type="source_control")
        result = PostgresIntegrationRepository._row_to_connection(row)

        assert result.integration_type == IntegrationType.SOURCE_CONTROL

    def test_slug_defaults_to_empty(self):
        defaults = {
            "id": "conn-001",
            "owner_id": "user-1",
            "integration_type": "issue_tracker",
            "adapter": "some.Adapter",
            "credential_name": "cred",
            "config": "{}",
            "enabled": True,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        row = MagicMock()
        row.__getitem__ = lambda self, key: defaults[key]
        row.get = lambda key, default=None: defaults.get(key, default)

        result = PostgresIntegrationRepository._row_to_connection(row)

        assert result.slug == ""
