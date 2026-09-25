"""Tests for ravn.adapters.trigger_store (file + postgres)."""

from __future__ import annotations

import pytest

from ravn.adapters.trigger_store.file_store import FileTriggerStore
from ravn.adapters.trigger_store.postgres_store import PostgresTriggerStore


class FakeAsyncpgPool:
    """Minimal fake asyncpg pool backed by an in-memory dict."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def fetch(self, query: str, tenant_id: str):
        del query
        rows = [row for row in self.rows.values() if row["tenant_id"] == tenant_id]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    async def fetchrow(self, query: str, *args):
        if "INSERT" in query:
            (trigger_id, kind, persona_name, spec, repo, enabled, owner_id, tenant_id) = args
            from datetime import UTC, datetime

            row = {
                "id": trigger_id,
                "kind": kind,
                "persona_name": persona_name,
                "spec": spec,
                "repo": repo,
                "enabled": enabled,
                "owner_id": owner_id,
                "tenant_id": tenant_id,
                "created_at": datetime.now(UTC),
            }
            self.rows[trigger_id] = row
            return row
        (trigger_id,) = args
        return self.rows.get(trigger_id)

    async def execute(self, query: str, *args):
        del query
        (trigger_id,) = args
        if trigger_id in self.rows:
            del self.rows[trigger_id]
            return "DELETE 1"
        return "DELETE 0"


@pytest.fixture(params=["file", "postgres"])
def store(request, tmp_path):
    if request.param == "file":
        return FileTriggerStore(tmp_path / "triggers.json")
    return PostgresTriggerStore(FakeAsyncpgPool())


class TestTriggerStoreCrud:
    async def test_create_then_list_scoped_by_tenant(self, store) -> None:
        await store.create_trigger(
            kind="cron",
            persona_name="p1",
            spec="*/15 * * * *",
            enabled=True,
            owner_id="user-a",
            tenant_id="tenant-a",
        )
        await store.create_trigger(
            kind="cron",
            persona_name="p2",
            spec="0 2 * * *",
            enabled=True,
            owner_id="user-b",
            tenant_id="tenant-b",
        )

        tenant_a_triggers = await store.list_triggers(tenant_id="tenant-a")
        assert len(tenant_a_triggers) == 1
        assert tenant_a_triggers[0].persona_name == "p1"

        tenant_b_triggers = await store.list_triggers(tenant_id="tenant-b")
        assert len(tenant_b_triggers) == 1
        assert tenant_b_triggers[0].persona_name == "p2"

    async def test_get_trigger_ignores_tenant(self, store) -> None:
        created = await store.create_trigger(
            kind="event",
            persona_name="p1",
            spec="github.pull_request.opened",
            enabled=True,
            owner_id="user-a",
            tenant_id="tenant-a",
        )
        fetched = await store.get_trigger(created.id)
        assert fetched is not None
        assert fetched.tenant_id == "tenant-a"

    async def test_get_trigger_missing_returns_none(self, store) -> None:
        assert await store.get_trigger("00000000-0000-0000-0000-000000000000") is None

    async def test_delete_trigger(self, store) -> None:
        created = await store.create_trigger(
            kind="cron",
            persona_name="p1",
            spec="0 2 * * *",
            enabled=True,
            owner_id="user-a",
            tenant_id="tenant-a",
        )
        assert await store.delete_trigger(created.id) is True
        assert await store.get_trigger(created.id) is None

    async def test_delete_missing_trigger_returns_false(self, store) -> None:
        assert await store.delete_trigger("00000000-0000-0000-0000-000000000000") is False


class TestFileTriggerStoreDurability:
    async def test_survives_process_restart(self, tmp_path) -> None:
        path = tmp_path / "triggers.json"
        store_a = FileTriggerStore(path)
        created = await store_a.create_trigger(
            kind="cron",
            persona_name="p1",
            spec="0 2 * * *",
            enabled=True,
            owner_id="user-a",
            tenant_id="tenant-a",
        )

        store_b = FileTriggerStore(path)
        reloaded = await store_b.get_trigger(created.id)
        assert reloaded == created

    async def test_writes_are_atomic_no_partial_file_left_behind(self, tmp_path) -> None:
        path = tmp_path / "triggers.json"
        store = FileTriggerStore(path)
        await store.create_trigger(
            kind="cron",
            persona_name="p1",
            spec="0 2 * * *",
            enabled=True,
            owner_id="user-a",
            tenant_id="tenant-a",
        )
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    async def test_corrupt_json_raises_instead_of_silently_starting_empty(self, tmp_path) -> None:
        path = tmp_path / "triggers.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = FileTriggerStore(path)
        with pytest.raises(ValueError, match="not readable/valid JSON"):
            await store.list_triggers(tenant_id="tenant-a")

    async def test_entry_missing_a_field_raises_instead_of_being_skipped(self, tmp_path) -> None:
        import json

        path = tmp_path / "triggers.json"
        path.write_text(
            json.dumps({"items": [{"id": "x", "kind": "cron"}]}),
            encoding="utf-8",
        )
        store = FileTriggerStore(path)
        with pytest.raises(ValueError, match="missing"):
            await store.list_triggers(tenant_id="tenant-a")
