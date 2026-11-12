"""The stores behind a project's memory: SQL on a mocked pool, the registry file, the builder."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from volundr.adapters.outbound.mimir_registry_directory import MimirRegistryFileDirectory
from volundr.adapters.outbound.postgres_project_memory import PostgresProjectMemoryRepository
from volundr.composition_builders import create_project_memory
from volundr.config import Settings
from volundr.domain.project_memory import (
    MemoryAccess,
    MemoryMountExistsError,
    ProjectMemoryMount,
    ProjectMemoryResolver,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
PROJECT = uuid4()


def row(**overrides) -> dict:
    now = datetime.now(UTC)
    values = {
        "project_id": PROJECT,
        "name": "notes",
        "registry_entry_id": "team-notes",
        "access": "read_write",
        "write_prefixes": '["project/", "entity/"]',
        "added_by": "alice",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return values


def pool():
    mock = MagicMock()
    mock.fetch = AsyncMock(return_value=[])
    mock.fetchrow = AsyncMock()
    mock.execute = AsyncMock()
    return mock


# --- SQL ---------------------------------------------------------------------------------


async def test_list_get_and_row_mapping():
    p = pool()
    p.fetch.return_value = [row(), row(name="b", write_prefixes=["x/"], access="read_write")]
    repo = PostgresProjectMemoryRepository(pool=p)

    mounts = await repo.list_for_project(PROJECT)

    assert [m.name for m in mounts] == ["notes", "b"]
    assert mounts[0].write_prefixes == ("project/", "entity/")
    assert mounts[1].write_prefixes == ("x/",)
    assert "ORDER BY created_at" in p.fetch.await_args.args[0]
    p.fetchrow.return_value = row(access="read", write_prefixes="[]")
    assert (await repo.get(PROJECT, "notes")).access is MemoryAccess.READ
    p.fetchrow.return_value = None
    assert await repo.get(PROJECT, "ghost") is None


async def test_add_writes_every_field_and_names_a_taken_name():
    p = pool()
    p.fetchrow.return_value = row()
    repo = PostgresProjectMemoryRepository(pool=p)
    mount = ProjectMemoryMount(
        project_id=PROJECT,
        name="notes",
        registry_entry_id="team-notes",
        access=MemoryAccess.READ_WRITE,
        write_prefixes=("project/", "entity/"),
        added_by="alice",
    )

    stored = await repo.add(mount)

    sql, *args = p.fetchrow.await_args.args
    assert "INSERT INTO project_memory_mounts" in sql
    assert args[:5] == [PROJECT, "notes", "team-notes", "read_write", '["project/", "entity/"]']
    assert stored.name == "notes"

    p.fetchrow.side_effect = asyncpg.UniqueViolationError("duplicate")
    with pytest.raises(MemoryMountExistsError, match="'notes'"):
        await repo.add(mount)


async def test_update_and_remove():
    p = pool()
    repo = PostgresProjectMemoryRepository(pool=p)
    p.fetchrow.return_value = row(access="read", write_prefixes="[]")
    updated = await repo.update(PROJECT, "notes", access=MemoryAccess.READ, write_prefixes=())
    assert updated.access is MemoryAccess.READ
    assert p.fetchrow.await_args.args[3:] == ("read", "[]")
    p.fetchrow.return_value = None
    assert await repo.update(PROJECT, "x", access=MemoryAccess.READ, write_prefixes=()) is None

    p.execute.return_value = "DELETE 1"
    assert await repo.remove(PROJECT, "notes") is True
    p.execute.return_value = "DELETE 0"
    assert await repo.remove(PROJECT, "notes") is False


def test_the_migration_is_idempotent_and_reversible():
    up = (MIGRATIONS / "000122_project_memory_mounts.up.sql").read_text()
    down = (MIGRATIONS / "000122_project_memory_mounts.down.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS project_memory_mounts" in up
    assert "PRIMARY KEY (project_id, name)" in up
    assert "REFERENCES forge_projects(id) ON DELETE CASCADE" in up
    assert "DROP TABLE IF EXISTS project_memory_mounts" in down


# --- the registry file -------------------------------------------------------------------


def registry(tmp_path, *entries: MimirRegistryEntry) -> Path:
    path = tmp_path / ".mimir-registry.json"
    store = MimirRegistryStore(path)
    for entry in entries:
        store.save_entry(entry)
    return path


async def test_the_directory_reads_entries_for_a_tenant(tmp_path):
    path = registry(
        tmp_path,
        MimirRegistryEntry(
            id="e-acme",
            name="acme-notes",
            tenant_id="acme",
            url="https://m.example",
            adapter="a.B",
            kwargs={"k": 1},
            secret_kwargs_env={"S": "E"},
            auth_ref="cred",
            categories=["entity"],
            default_read_priority=3,
        ),
        MimirRegistryEntry(id="e-other", name="other", tenant_id="globex", path="/o"),
        MimirRegistryEntry(id="e-global", name="shared", path="/g", enabled=False),
    )
    directory = MimirRegistryFileDirectory(registry_path=str(path))

    target = await directory.resolve("e-acme", "acme")
    assert (target.id, target.url, target.adapter, target.kwargs) == (
        "e-acme",
        "https://m.example",
        "a.B",
        {"k": 1},
    )
    assert target.secret_kwargs_env == {"S": "E"} and target.auth_ref == "cred"
    assert target.categories == ("entity",) and target.default_read_priority == 3
    assert await directory.resolve("e-other", "acme") is None
    assert await directory.resolve("missing", "acme") is None
    assert (await directory.resolve("e-global", "acme")).enabled is False
    assert (await directory.resolve("e-global", "")).path == "/g"


async def test_a_registry_that_cannot_be_read_raises(tmp_path):
    directory = MimirRegistryFileDirectory(registry_path=str(tmp_path / "absent.json"))
    with pytest.raises(FileNotFoundError, match="registry_path"):
        await directory.resolve("e", "acme")
    with pytest.raises(ValueError, match="registry_path is empty"):
        MimirRegistryFileDirectory(registry_path=" ")


# --- the builder -------------------------------------------------------------------------


def test_the_builder_wires_the_configured_adapters(tmp_path):
    settings = Settings()
    settings.projects.memory.directory_kwargs = {"registry_path": str(tmp_path / "r.json")}

    repository, resolver = create_project_memory(settings, pool())

    assert isinstance(repository, PostgresProjectMemoryRepository)
    assert isinstance(resolver, ProjectMemoryResolver)


def test_the_builder_does_nothing_when_projects_are_off():
    settings = Settings()
    settings.projects.enabled = False
    assert create_project_memory(settings, pool()) == (None, None)


def test_an_adapter_of_the_wrong_type_is_fatal_at_startup():
    settings = Settings()
    settings.projects.memory.repository_adapter = (
        "volundr.adapters.outbound.postgres_project_members.PostgresProjectMemberRepository"
    )
    settings.projects.memory.repository_kwargs = {}
    with pytest.raises(TypeError, match="ProjectMemoryRepository"):
        create_project_memory(settings, pool())
    settings = Settings()
    settings.projects.memory.directory_adapter = "pathlib.PurePath"
    settings.projects.memory.directory_kwargs = {}
    with pytest.raises(TypeError, match="MemoryMountDirectory"):
        create_project_memory(settings, pool())


def test_the_registry_file_round_trips_as_json(tmp_path):
    path = registry(tmp_path, MimirRegistryEntry(id="x", name="n", path="/p"))
    assert json.loads(path.read_text())[0]["id"] == "x"
