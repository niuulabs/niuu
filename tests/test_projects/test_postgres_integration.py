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
