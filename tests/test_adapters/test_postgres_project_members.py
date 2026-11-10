"""Project member SQL, exercised against a mocked pool (no database container)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from niuu.domain.agent_directory import AgentSkill
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.adapters.outbound.postgres_project_members import PostgresProjectMemberRepository
from volundr.adapters.outbound.postgres_projects import PostgresProjectRepository
from volundr.domain.project_ports import LastOwnerError, ProjectConflictError
from volundr.domain.projects import ForgeProject, ProjectMember, ProjectRole

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
PROJECT = uuid4()


class _Context:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        return False


def mock_pool():
    connection = MagicMock()
    connection.fetch = AsyncMock(return_value=[])
    connection.fetchrow = AsyncMock()
    connection.execute = AsyncMock()
    connection.transaction = MagicMock(return_value=_Context())
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock()
    pool.get_max_size = MagicMock(return_value=10)
    pool.acquire = MagicMock(return_value=_Context(connection))
    return pool, connection


def member_row(**overrides) -> dict:
    now = datetime.now(UTC)
    row = {
        "project_id": PROJECT,
        "user_id": "bob",
        "tenant_id": "acme",
        "role": "viewer",
        "expertise": "",
        "skills": "[]",
        "added_by": "alice",
        "created_at": now,
        "updated_at": now,
        "display_name": "Bob",
        "email": "bob@x.test",
    }
    row.update(overrides)
    return row


def member(**overrides) -> ProjectMember:
    values = {
        "project_id": PROJECT,
        "user_id": "bob",
        "tenant_id": "acme",
        "role": ProjectRole.VIEWER,
        "added_by": "alice",
    }
    values.update(overrides)
    return ProjectMember(**values)


# --- member repository ----------------------------------------------------------------


async def test_list_for_project_joins_the_profile_and_parses_skills():
    pool, _ = mock_pool()
    skills = json.dumps([{"id": "db", "name": "Databases"}])
    pool.fetch.return_value = [member_row(skills=skills), member_row(user_id="eve", skills=[])]
    members = await PostgresProjectMemberRepository(pool).list_for_project(PROJECT)
    assert [m.user_id for m in members] == ["bob", "eve"]
    assert members[0].skills[0].id == "db" and members[0].display_name == "Bob"
    sql, *params = pool.fetch.call_args.args
    assert "LEFT JOIN users u ON u.id = m.user_id" in sql and "project_id = $1" in sql
    assert params == [PROJECT]


async def test_list_for_user_is_bounded_to_the_tenant():
    pool, _ = mock_pool()
    pool.fetch.return_value = [member_row()]
    found = await PostgresProjectMemberRepository(pool).list_for_user("bob", "acme")
    assert [m.project_id for m in found] == [PROJECT]
    sql, *params = pool.fetch.call_args.args
    assert "m.user_id = $1 AND m.tenant_id = $2" in sql and params == ["bob", "acme"]


async def test_get_returns_none_when_absent():
    pool, _ = mock_pool()
    pool.fetchrow.return_value = None
    assert await PostgresProjectMemberRepository(pool).get(PROJECT, "ghost") is None


async def test_add_inserts_then_reads_back_with_profile():
    pool, _ = mock_pool()
    pool.fetchrow.side_effect = [{"user_id": "bob"}, member_row(role="approver")]
    skill = AgentSkill(id="db", name="Databases")
    stored = await PostgresProjectMemberRepository(pool).add(
        member(role=ProjectRole.APPROVER, expertise="pg", skills=[skill])
    )
    assert stored.role == ProjectRole.APPROVER and stored.display_name == "Bob"
    sql, *params = pool.fetchrow.call_args_list[0].args
    assert "ON CONFLICT (project_id, user_id) DO NOTHING" in sql
    assert params[:5] == [PROJECT, "bob", "acme", "approver", "pg"]
    assert [skill["id"] for skill in json.loads(params[5])] == ["db"]


async def test_add_conflicts_when_already_a_member():
    pool, _ = mock_pool()
    pool.fetchrow.return_value = None
    with pytest.raises(ProjectConflictError):
        await PostgresProjectMemberRepository(pool).add(member())


async def test_add_conflicts_when_removed_while_adding():
    pool, _ = mock_pool()
    pool.fetchrow.side_effect = [{"user_id": "bob"}, None]
    with pytest.raises(ProjectConflictError):
        await PostgresProjectMemberRepository(pool).add(member())


async def test_update_locks_owner_rows_and_changes_only_named_fields():
    pool, connection = mock_pool()
    connection.fetch.return_value = [{"user_id": "alice"}, {"user_id": "bob"}]
    connection.fetchrow.return_value = {"user_id": "bob"}
    pool.fetchrow.return_value = member_row(role="viewer", expertise="Go")
    updated = await PostgresProjectMemberRepository(pool).update(
        PROJECT, "bob", role=ProjectRole.VIEWER, expertise="Go"
    )
    assert updated.expertise == "Go"
    assert "FOR UPDATE" in connection.fetch.call_args.args[0]
    sql, *params = connection.fetchrow.call_args.args
    assert "COALESCE($3, role)" in sql and "COALESCE($5::jsonb, skills)" in sql
    assert params == [PROJECT, "bob", "viewer", "Go", None]


async def test_update_serializes_skills_when_given():
    pool, connection = mock_pool()
    connection.fetchrow.return_value = {"user_id": "bob"}
    pool.fetchrow.return_value = member_row()
    await PostgresProjectMemberRepository(pool).update(
        PROJECT, "bob", skills=[AgentSkill(id="x", name="X")]
    )
    assert json.loads(connection.fetchrow.call_args.args[-1])[0]["id"] == "x"


async def test_update_of_a_missing_member_is_none():
    pool, connection = mock_pool()
    connection.fetchrow.return_value = None
    assert await PostgresProjectMemberRepository(pool).update(PROJECT, "x", expertise="y") is None


async def test_demoting_the_last_owner_raises_before_writing():
    pool, connection = mock_pool()
    connection.fetch.return_value = [{"user_id": "alice"}]
    with pytest.raises(LastOwnerError):
        await PostgresProjectMemberRepository(pool).update(
            PROJECT, "alice", role=ProjectRole.APPROVER
        )
    connection.fetchrow.assert_not_awaited()


async def test_keeping_the_last_owner_an_owner_is_allowed():
    pool, connection = mock_pool()
    connection.fetch.return_value = [{"user_id": "alice"}]
    connection.fetchrow.return_value = {"user_id": "alice"}
    pool.fetchrow.return_value = member_row(user_id="alice", role="owner")
    result = await PostgresProjectMemberRepository(pool).update(
        PROJECT, "alice", role=ProjectRole.OWNER
    )
    assert result.role == ProjectRole.OWNER


async def test_remove_deletes_unless_it_is_the_last_owner():
    pool, connection = mock_pool()
    connection.fetch.return_value = [{"user_id": "alice"}]
    connection.fetchrow.return_value = {"user_id": "bob"}
    repo = PostgresProjectMemberRepository(pool)
    removed_bob = await repo.remove(PROJECT, "bob")
    assert removed_bob is True
    assert "DELETE FROM project_members" in connection.fetchrow.call_args.args[0]
    connection.fetchrow.return_value = None
    removed_ghost = await repo.remove(PROJECT, "ghost")
    assert removed_ghost is False
    with pytest.raises(LastOwnerError):
        await repo.remove(PROJECT, "alice")


async def test_candidates_are_active_tenant_members_outside_the_project():
    pool, _ = mock_pool()
    pool.fetch.return_value = [{"id": "carol", "display_name": "Carol", "email": "c@x.test"}]
    found = await PostgresProjectMemberRepository(pool).candidates(PROJECT, "acme", "car")
    assert [(c.user_id, c.display_name) for c in found] == [("carol", "Carol")]
    sql, *params = pool.fetch.call_args.args
    assert "u.status = 'active'" in sql and "tenant_memberships" in sql and "NOT EXISTS" in sql
    assert "ILIKE" not in sql  # no LIKE wildcards from user input
    assert params == [PROJECT, "acme", "car"]


# --- project repository ---------------------------------------------------------------


def project_for(owner="alice") -> ForgeProject:
    return ForgeProject(name="Lexi", owner_id=owner, tenant_id="acme")


async def test_register_records_the_creator_as_owner_in_one_transaction():
    pool, connection = mock_pool()
    project = project_for()
    connection.fetchrow.return_value = {"document": project.document_json()}
    stored = await PostgresProjectRepository(pool).register(project)
    assert stored.id == project.id
    connection.transaction.assert_called_once()
    sql, *params = connection.execute.call_args.args
    assert "INSERT INTO project_members" in sql and "'owner'" in sql
    assert params == [project.id, "alice", "acme"]


async def test_register_without_an_identity_records_no_member():
    pool, connection = mock_pool()
    project = project_for(owner="")
    connection.fetchrow.return_value = {"document": project.document_json()}
    await PostgresProjectRepository(pool).register(project)
    connection.execute.assert_not_awaited()


async def test_register_retry_returns_the_stored_project_without_a_second_member():
    pool, connection = mock_pool()
    project = project_for()
    connection.fetchrow.return_value = None
    pool.fetchrow.return_value = {"document": project.document_json()}
    stored = await PostgresProjectRepository(pool).register(project)
    assert stored.id == project.id
    connection.execute.assert_not_awaited()


async def test_list_member_projects_joins_on_membership_in_the_tenant():
    pool, _ = mock_pool()
    project = project_for()
    pool.fetch.return_value = [{"document": project.document_json()}]
    found = await PostgresProjectRepository(pool).list_member_projects("bob", "acme")
    assert [p.id for p in found] == [project.id]
    sql, *params = pool.fetch.call_args.args
    assert "JOIN project_members m" in sql and params == ["bob", "acme"]


# --- session repository ---------------------------------------------------------------


async def test_sessions_by_project_use_the_coordination_index():
    pool, _ = mock_pool()
    repo = PostgresSessionRepository(pool)
    assert await repo.list_by_project([], "acme") == []
    pool.fetch.assert_not_awaited()
    assert await repo.list_by_project([PROJECT], "acme") == []
    sql, *params = pool.fetch.call_args.args
    assert "(coordination->>'project_id') = ANY($2::text[])" in sql
    assert params == ["acme", [str(PROJECT)]]


# --- migration ------------------------------------------------------------------------


def test_migration_is_idempotent_cascading_and_backfills_creators():
    up = (MIGRATIONS / "000119_project_members.up.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS project_members" in up
    assert "REFERENCES forge_projects(id) ON DELETE CASCADE" in up
    assert "PRIMARY KEY (project_id, user_id)" in up
    assert "CHECK (role IN ('viewer', 'contributor', 'approver', 'owner'))" in up
    assert "CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id)" in up
    assert "ON CONFLICT (project_id, user_id) DO NOTHING" in up and "'owner'" in up
    down = (MIGRATIONS / "000119_project_members.down.sql").read_text()
    assert "DROP TABLE IF EXISTS project_members" in down
