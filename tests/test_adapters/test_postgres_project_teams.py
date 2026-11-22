"""Project team SQL against a mocked pool; tests/test_projects runs it on PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from niuu.adapters.postgres_project_teams import PostgresProjectTeamRecords
from niuu.domain.project_team import ProjectTeam, ProjectTeamRecord
from niuu.ports.project_teams import ProjectTeamHomeConflictError
from tests.test_adapters.test_postgres_project_members import member, mock_pool
from volundr.adapters.outbound.postgres_project_members import PostgresProjectMemberRepository
from volundr.adapters.outbound.postgres_project_team_sync import PostgresProjectTeamSyncRepository
from volundr.domain.project_ports import LastOwnerError
from volundr.domain.projects import ProjectRole

PROJECT = uuid4()
NOW = datetime(2026, 11, 22, tzinfo=UTC)


def _record(home="valhalla"):
    return ProjectTeamRecord(
        home_instance_id=home, team=ProjectTeam(project_id=PROJECT, tenant_id="acme", version=3)
    )


def _row(record):
    return {
        "home_instance_id": record.home_instance_id,
        "team": record.team.model_dump_json(),
        "project_id": record.team.project_id,
    }


def _pool(fetchrow=None, fetch=()):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow)
    pool.fetch = AsyncMock(return_value=list(fetch))
    pool.execute = AsyncMock()
    return pool


async def test_the_guild_keeps_one_team_per_project():
    pool = _pool(_row(_record()), [_row(_record())])
    records = PostgresProjectTeamRecords(pool)
    assert await records.put(_record()) == _record()
    sql, *args = pool.execute.await_args.args
    assert "guild_project_teams.home_instance_id = EXCLUDED.home_instance_id" in sql
    assert "guild_project_teams.version <= EXCLUDED.version" in sql
    assert args == [PROJECT, "valhalla", 3, _record().team.model_dump_json()]
    assert await records.of_member("alice", "acme") == [_record()]
    assert pool.fetch.await_args.args[1:] == ("alice", "acme")
    assert await records.recorded([PROJECT]) == {PROJECT}
    assert await PostgresProjectTeamRecords(_pool()).get(PROJECT) is None


async def test_the_guild_refuses_another_home_and_a_lost_row():
    with pytest.raises(ProjectTeamHomeConflictError, match="published by Forge valhalla"):
        await PostgresProjectTeamRecords(_pool(_row(_record()))).put(_record("noatun"))
    with pytest.raises(RuntimeError, match="was removed"):
        await PostgresProjectTeamRecords(_pool()).put(_record())


async def test_where_a_team_stands_is_kept_per_project():
    row = {"version": 2, "pending": True, "synced_at": NOW, "error": ""}
    pool = _pool(row)
    sync = PostgresProjectTeamSyncRepository(pool)
    for written in (
        sync.get(PROJECT),
        sync.changed(PROJECT),
        sync.published(PROJECT, 2, NOW),
        sync.copied(PROJECT, 2, NOW),
        sync.failed(PROJECT, "away"),
    ):
        assert (await written).model_dump() == row
        assert pool.fetchrow.await_args.args[1] == PROJECT
    assert (await PostgresProjectTeamSyncRepository(_pool()).get(PROJECT)).synced_at is None


async def test_a_copys_people_are_replaced_whole_and_always_have_an_owner():
    pool, connection = mock_pool()
    connection.executemany = AsyncMock()
    members = PostgresProjectMemberRepository(pool)
    owner = member(user_id="alice", role=ProjectRole.OWNER)
    await members.replace(PROJECT, [owner])
    connection.transaction.assert_called_once()
    assert connection.execute.await_args.args[1:] == (PROJECT, ["alice"])
    [row] = connection.executemany.await_args.args[1]
    assert row[:4] == (PROJECT, "alice", owner.tenant_id, "owner")
    with pytest.raises(LastOwnerError):
        await members.replace(PROJECT, [member(role=ProjectRole.VIEWER)])
