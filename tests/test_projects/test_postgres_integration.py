"""Real database acceptance in a throwaway schema, never in user session tables."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from tests.test_adapters.test_message_delivery_postgres_integration import (
    isolated_pool as postgres_pool_fixture,
)
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.adapters.outbound.postgres_projects import PostgresProjectRepository
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.domain.models import Session
from volundr.domain.project_ports import ProjectConflictError
from volundr.domain.projects import (
    ForgeProject,
    ProjectReceipt,
    SessionCoordination,
    SessionReference,
)

pytestmark = pytest.mark.integration
isolated_pool = postgres_pool_fixture
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


async def seed(pool):
    async with pool.acquire() as connection:
        await apply_startup_migrations(connection, sorted(MIGRATIONS.glob("*.up.sql")))
    repo = PostgresProjectRepository(pool, dispatch_poll_seconds=0.001)
    project = await repo.register(
        ForgeProject(slug="lexi", name="Lexi", repo_url="https://github.com/xteo/project-lexi")
    )
    return repo, project


async def test_migration_and_session_roundtrip(isolated_pool):
    _, project = await seed(isolated_pool)
    sessions = PostgresSessionRepository(isolated_pool)
    legacy = await sessions.create(Session(name="legacy"))
    coordinator = await sessions.create(
        Session(
            name="coordinator",
            coordination=SessionCoordination(project_id=project.id, role="coordinator"),
            workload_config={"project_context": "Persist this checkpoint"},
        )
    )
    restored = PostgresSessionRepository(isolated_pool)
    assert (await restored.get(legacy.id)).coordination is None
    actual = await restored.get(coordinator.id)
    assert actual.coordination.role == "coordinator"
    assert actual.workload_config["project_context"] == "Persist this checkpoint"
    await restored.update(actual.model_copy(update={"name": "replaced"}))
    assert (await restored.get(actual.id)).coordination.project_id == project.id
    # Reapplying the identical numbered set is a no-op.
    async with isolated_pool.acquire() as connection:
        await apply_startup_migrations(connection, sorted(MIGRATIONS.glob("*.up.sql")))


async def test_assignment_race_and_stale_lifecycle_writer_preserve_membership(isolated_pool):
    _, project = await seed(isolated_pool)
    sessions = PostgresSessionRepository(isolated_pool)
    session = await sessions.create(
        Session(
            name="running-worker",
            workload_config={"effort": "high", "project_context": "old"},
        )
    )
    coordination = SessionCoordination(project_id=project.id)
    results = await asyncio.gather(
        *(sessions.update_coordination(session, coordination) for _ in range(2))
    )
    assert sum(result is not None for result in results) == 1
    updated = await sessions.update(session.model_copy(update={"name": "renamed"}))
    assert updated.coordination == coordination and updated.coordination_revision == 1
    assert updated.workload_config == {"effort": "high"}
    assert (await sessions.get(session.id)) == updated


async def test_more_dispatchers_than_pool_connections_do_not_deadlock(isolated_pool):
    repo, _ = await seed(isolated_pool)
    keys = [uuid4() for _ in range(12)]

    async def dispatch(key):
        async with repo.dispatch_lock(key):
            completed = await repo.claim_dispatch(key, "a" * 64, uuid4())
            await asyncio.sleep(0.005)
            await repo.complete_dispatch(key)
            return completed

    result = await asyncio.wait_for(asyncio.gather(*(dispatch(key) for key in keys)), timeout=10)
    assert result == [False] * len(keys)
    result = await asyncio.wait_for(
        asyncio.gather(*(dispatch(keys[0]) for _ in range(12))), timeout=10
    )
    assert all(result)
    fresh = PostgresProjectRepository(isolated_pool)
    assert await fresh.claim_dispatch(keys[0], "a" * 64, uuid4())
    with pytest.raises(ProjectConflictError):
        await fresh.claim_dispatch(keys[0], "b" * 64, uuid4())


async def test_receipt_deduplication_cursor_and_recovery(isolated_pool):
    repo, project = await seed(isolated_pool)
    sender = SessionReference(instance_id="thor", session_id=uuid4())
    receipts = [
        ProjectReceipt(project_id=project.id, sender=sender, content=f"Result {i}")
        for i in range(24)
    ]
    await asyncio.gather(*(repo.put_receipt(item) for item in receipts))
    await asyncio.gather(*(repo.put_receipt(receipts[0]) for _ in range(12)))
    recovered = PostgresProjectRepository(isolated_pool)
    cursor = 0
    found = []
    while page := await recovered.receipts(project.id, cursor, 5):
        found.extend(page)
        cursor = page[-1]["seq"]
    assert len(found) == 24 and len({item["id"] for item in found}) == 24
    assert await recovered.acknowledge(project.id, receipts[0].id)
    assert (await recovered.put_receipt(receipts[0])).acknowledged_at is not None
    with pytest.raises(ProjectConflictError):
        await recovered.put_receipt(receipts[0].model_copy(update={"content": "Changed"}))


async def test_project_compare_and_swap_and_registration_conflict(isolated_pool):
    repo, project = await seed(isolated_pool)
    assert (await repo.register(project)).id == project.id
    with pytest.raises(ProjectConflictError):
        await repo.register(project.model_copy(update={"owner_id": "another-user"}))
    updates = [project.model_copy(update={"name": str(i), "revision": 2}) for i in range(2)]
    results = await asyncio.gather(
        *(repo.update(item, 1) for item in updates), return_exceptions=True
    )
    assert sum(isinstance(result, ForgeProject) for result in results) == 1
    assert sum(isinstance(result, ProjectConflictError) for result in results) == 1


async def test_late_usage_write_cannot_restore_working_after_idle(isolated_pool):
    from datetime import UTC, datetime, timedelta

    from volundr.domain.models import SessionActivityState

    await seed(isolated_pool)
    repo = PostgresSessionRepository(isolated_pool)
    start = datetime.now(UTC)
    busy = await repo.create(
        Session(
            name="activity-race",
            activity_state=SessionActivityState.ACTIVE,
            activity_state_since=start,
            turn_started_at=start,
            last_active=start,
        )
    )
    finish = start + timedelta(minutes=2)
    idle = await repo.update(
        busy.model_copy(
            update={
                "activity_state": SessionActivityState.IDLE,
                "activity_state_since": finish,
                "turn_started_at": None,
                "last_active": finish,
            }
        )
    )
    late = await repo.update(busy.model_copy(update={"tokens_used": 100}))
    assert late.activity_state == idle.activity_state == SessionActivityState.IDLE
    assert late.activity_state_since == finish and late.turn_started_at is None
    assert late.last_active == finish and late.tokens_used == 100
    assert (await repo.get(busy.id)).activity_state == SessionActivityState.IDLE


async def test_lightweight_project_document_and_detach_roundtrip(isolated_pool):
    repo, _ = await seed(isolated_pool)
    garden = await repo.register(ForgeProject(slug="garden", name="Garden", brief="Grow."))
    async with isolated_pool.acquire() as connection:
        stored = await connection.fetchval(
            "SELECT document::text FROM forge_projects WHERE id=$1", garden.id
        )
    assert '"brief": "Grow."' in stored and '"repo_url": ""' in stored
    assert "home_instance_id" not in stored
    assert (await PostgresProjectRepository(isolated_pool).get(garden.id)) == garden
    sessions = PostgresSessionRepository(isolated_pool)
    member = await sessions.create(
        Session(name="member", coordination=SessionCoordination(project_id=garden.id))
    )
    detached = await sessions.update_coordination(member, None)
    assert detached.coordination is None and detached.coordination_revision == 1
    assert (await sessions.get(member.id)).coordination is None


async def test_a_projects_team_is_kept_and_copied(isolated_pool):
    from datetime import UTC, datetime

    from niuu.adapters.postgres_project_teams import PostgresProjectTeamRecords
    from niuu.domain.project_team import ProjectTeam, ProjectTeamRecord, TeamMember
    from niuu.ports.project_teams import ProjectTeamHomeConflictError
    from volundr.adapters.outbound.postgres_project_members import PostgresProjectMemberRepository
    from volundr.adapters.outbound.postgres_project_team_sync import (
        PostgresProjectTeamSyncRepository,
    )
    from volundr.domain.projects import ProjectMember, ProjectRole

    repo, _ = await seed(isolated_pool)
    project = await repo.register(
        ForgeProject(slug="team", name="Team", owner_id="cara", tenant_id="acme")
    )
    now = datetime.now(UTC)

    # A copy's people are replaced whole: its creator goes, the home's people come.
    members = PostgresProjectMemberRepository(isolated_pool)
    people = [
        ProjectMember(
            project_id=project.id,
            user_id=user,
            tenant_id="acme",
            role=role,
            added_by="alice",
            created_at=now,
            updated_at=now,
        )
        for user, role in (("alice", ProjectRole.OWNER), ("bob", ProjectRole.VIEWER))
    ]
    for _ in range(2):
        await members.replace(project.id, people)
        held = await members.list_for_project(project.id)
        assert {m.user_id: m.role for m in held} == {
            "alice": ProjectRole.OWNER,
            "bob": ProjectRole.VIEWER,
        }

    sync = PostgresProjectTeamSyncRepository(isolated_pool)
    assert (await sync.get(project.id)).synced_at is None
    assert (await sync.changed(project.id)).version == 1
    assert (await sync.changed(project.id)).pending is True
    assert (await sync.failed(project.id, "away")).error == "away"
    assert (await sync.published(project.id, 1, now)).pending is True
    done = await sync.published(project.id, 2, now)
    assert (done.version, done.pending, done.error) == (2, False, "")
    copied = await sync.copied(project.id, 7, now)
    assert (copied.version, copied.synced_at) == (7, now)

    records = PostgresProjectTeamRecords(isolated_pool)

    def record(version, home="valhalla", people=("alice", "bob")):
        return ProjectTeamRecord(
            home_instance_id=home,
            team=ProjectTeam(
                project_id=project.id,
                tenant_id="acme",
                version=version,
                members=[
                    TeamMember(user_id=user, role="owner", created_at=now, updated_at=now)
                    for user in people
                ],
            ),
        )

    assert await records.get(project.id) is None
    assert (await records.put(record(2))).team.version == 2
    assert (await records.put(record(1))).team.version == 2
    assert (await records.put(record(3, people=("alice",)))).team.version == 3
    with pytest.raises(ProjectTeamHomeConflictError):
        await records.put(record(9, home="noatun"))
    assert (await records.get(project.id)) == record(3, people=("alice",))
    assert await records.of_member("alice", "acme") == [record(3, people=("alice",))]
    assert await records.of_member("alice", "other") == []
    assert await records.of_member("bob", "acme") == []
    assert await records.recorded([project.id, uuid4()]) == {project.id}
