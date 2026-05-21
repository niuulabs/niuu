"""Tests for PostgreSQL-backed instance repository."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from niuu.adapters.postgres_instances import PostgresInstanceRepository
from niuu.domain.models import InstanceKind, InstanceVisibility, RegisteredInstance


class FakePool:
    def __init__(self) -> None:
        self.fetch_result: list[dict] = []
        self.fetchrow_result: dict | None = None
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict]:
        self.fetch_calls.append((query, args))
        return self.fetch_result

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        self.fetchrow_calls.append((query, args))
        return self.fetchrow_result

    async def execute(self, query: str, *args: object) -> None:
        self.execute_calls.append((query, args))


def _row(
    instance_id: str,
    *,
    kind: str = "volundr",
    config: dict | str | None = None,
) -> dict:
    now = datetime.now(UTC)
    return {
        "id": instance_id,
        "kind": kind,
        "slug": f"{instance_id}-slug",
        "name": f"Instance {instance_id}",
        "base_url": f"https://{instance_id}.example.com",
        "visibility": "tenant",
        "owner_id": None,
        "tenant_id": "tenant-a",
        "enabled": True,
        "is_default": False,
        "config": {} if config is None else config,
        "created_at": now,
        "updated_at": now,
    }


def _instance(instance_id: str) -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id=instance_id,
        kind=InstanceKind.VOLUNDR,
        slug=f"{instance_id}-slug",
        name=f"Instance {instance_id}",
        base_url=f"https://{instance_id}.example.com",
        visibility=InstanceVisibility.TENANT,
        owner_id=None,
        tenant_id="tenant-a",
        enabled=True,
        is_default=False,
        config={"region": "ca-central-1"},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_list_instances_uses_expected_queries_and_maps_rows() -> None:
    pool = FakePool()
    pool.fetch_result = [_row("volundr-1"), _row("ting-1", kind="ting")]
    repo = PostgresInstanceRepository(pool)

    all_instances = await repo.list_instances()
    volundr_instances = await repo.list_instances(InstanceKind.VOLUNDR)

    assert [item.id for item in all_instances] == ["volundr-1", "ting-1"]
    assert [item.kind for item in all_instances] == [InstanceKind.VOLUNDR, InstanceKind.TING]
    assert [item.id for item in volundr_instances] == ["volundr-1", "ting-1"]
    assert "WHERE kind = $1" not in pool.fetch_calls[0][0]
    assert "WHERE kind = $1" in pool.fetch_calls[1][0]
    assert pool.fetch_calls[1][1] == ("volundr",)


@pytest.mark.asyncio
async def test_get_instance_returns_none_or_mapped_instance() -> None:
    pool = FakePool()
    repo = PostgresInstanceRepository(pool)

    assert await repo.get_instance("missing") is None

    pool.fetchrow_result = _row("instance-1", config='{"region":"eu-west-1"}')
    instance = await repo.get_instance("instance-1")

    assert instance is not None
    assert instance.id == "instance-1"
    assert instance.config == {"region": "eu-west-1"}
    assert pool.fetchrow_calls[-1][1] == ("instance-1",)


@pytest.mark.asyncio
async def test_save_and_delete_instance_send_expected_sql_payloads() -> None:
    pool = FakePool()
    repo = PostgresInstanceRepository(pool)
    instance = _instance(str(uuid4()))

    saved = await repo.save_instance(instance)
    await repo.delete_instance(instance.id)

    assert saved == instance
    insert_query, insert_args = pool.execute_calls[0]
    assert "INSERT INTO niuu_instances" in insert_query
    assert insert_args[0] == instance.id
    assert insert_args[1] == "volundr"
    assert insert_args[5] == "tenant"
    assert insert_args[10] == '{"region": "ca-central-1"}'

    delete_query, delete_args = pool.execute_calls[1]
    assert "DELETE FROM niuu_instances" in delete_query
    assert delete_args == (instance.id,)


def test_row_to_instance_handles_mapping_and_json_string_configs() -> None:
    mapping_instance = PostgresInstanceRepository._row_to_instance(
        _row("mapped", config={"enabled": True})
    )
    json_instance = PostgresInstanceRepository._row_to_instance(
        _row("json", config='{"region":"us-east-1"}')
    )

    assert mapping_instance.config == {"enabled": True}
    assert json_instance.config == {"region": "us-east-1"}
