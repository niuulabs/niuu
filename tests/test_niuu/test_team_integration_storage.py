"""Postgres storage of team (project) connections and of their usage log."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from niuu.adapters.postgres_integration_usage import PostgresIntegrationUsageLog
from niuu.adapters.postgres_integrations import PostgresIntegrationRepository
from niuu.domain.models import (
    IntegrationConnection,
    IntegrationType,
    IntegrationUsage,
    project_owner_id,
)

PROJECT = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 11, 10, tzinfo=UTC)


def team_connection() -> IntegrationConnection:
    return IntegrationConnection(
        id="22222222-2222-4222-8222-222222222222",
        owner_id=project_owner_id(PROJECT),
        integration_type=IntegrationType.MESSAGING,
        adapter="",
        credential_name="team-slack-1",
        config={"channel": "#pay"},
        enabled=True,
        created_at=NOW,
        updated_at=NOW,
        slug="slack",
        project_id=PROJECT,
        connected_by="alice",
    )


async def test_a_team_connection_keeps_its_project_and_installer():
    pool = AsyncMock()
    repo = PostgresIntegrationRepository(pool)
    await repo.save_connection(team_connection())
    sql, *args = pool.execute.await_args.args
    assert "project_id" in sql and "connected_by" in sql
    assert args[-2:] == [PROJECT, "alice"]

    row = {
        "id": team_connection().id,
        "owner_id": project_owner_id(PROJECT),
        "integration_type": "messaging",
        "adapter": "",
        "credential_name": "team-slack-1",
        "config": '{"channel": "#pay"}',
        "enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
        "slug": "slack",
        "project_id": PROJECT,
        "connected_by": "alice",
    }
    pool.fetchrow.return_value = row
    loaded = await repo.get_connection(row["id"])
    assert loaded == team_connection()


async def test_a_personal_row_has_no_project():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "c",
        "owner_id": "alice",
        "integration_type": "issue_tracker",
        "adapter": "a",
        "credential_name": "n",
        "config": {},
        "enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
        "slug": "linear",
        "project_id": None,
        "connected_by": "",
    }
    loaded = await PostgresIntegrationRepository(pool).get_connection("c")
    assert loaded.project_id is None and loaded.connected_by == ""


async def test_usage_is_written_and_read_newest_first_with_a_cursor():
    pool = AsyncMock()
    log = PostgresIntegrationUsageLog(pool)
    await log.record(
        IntegrationUsage(
            connection_id="c",
            project_id=PROJECT,
            action="add_comment",
            acting_for="bob",
            actor="ting",
            subject="PAY-1",
        )
    )
    assert pool.execute.await_args.args[1:] == (
        "c",
        PROJECT,
        "bob",
        "ting",
        "add_comment",
        "PAY-1",
        "",
    )

    pool.fetch.return_value = [
        {
            "id": "u1",
            "connection_id": "c",
            "project_id": PROJECT,
            "acting_for": "bob",
            "actor": "ting",
            "action": "add_comment",
            "subject": "PAY-1",
            "session_id": "",
            "occurred_at": NOW,
        }
    ]
    [usage] = await log.list_for_project(PROJECT, limit=5, before=NOW)
    sql, *args = pool.fetch.await_args.args
    assert "ORDER BY occurred_at DESC" in sql and args == [PROJECT, NOW, 5]
    assert usage.subject == "PAY-1" and usage.occurred_at == NOW
