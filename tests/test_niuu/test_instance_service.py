"""Tests for the shared instance registry service."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.services.instances import (
    InstanceAccessError,
    InstanceService,
    InstanceValidationError,
)
from niuu.service_instances import seed_configured_instances


class InMemoryInstanceRepository:
    def __init__(self, instances: list[RegisteredInstance] | None = None) -> None:
        self.instances = {instance.id: instance for instance in instances or []}
        self.deleted_ids: list[str] = []

    async def list_instances(
        self,
        kind: InstanceKind | None = None,
    ) -> list[RegisteredInstance]:
        values = list(self.instances.values())
        if kind is None:
            return values
        return [instance for instance in values if instance.kind == kind]

    async def get_instance(self, instance_id: str) -> RegisteredInstance | None:
        return self.instances.get(instance_id)

    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        self.instances[instance.id] = instance
        return instance

    async def delete_instance(self, instance_id: str) -> None:
        self.deleted_ids.append(instance_id)
        self.instances.pop(instance_id, None)


def _principal(
    *,
    admin: bool = False,
    user_id: str = "user-a",
    tenant_id: str = "tenant-a",
) -> Principal:
    return Principal(
        user_id=user_id,
        email=f"{user_id}@example.com",
        tenant_id=tenant_id,
        roles=["volundr:admin"] if admin else [],
    )


def _instance(
    instance_id: str,
    *,
    kind: InstanceKind = InstanceKind.VOLUNDR,
    slug: str | None = None,
    visibility: InstanceVisibility = InstanceVisibility.TENANT,
    owner_id: str | None = None,
    tenant_id: str | None = "tenant-a",
    enabled: bool = True,
    is_default: bool = False,
    created_at: datetime | None = None,
    tags: list[str] | None = None,
) -> RegisteredInstance:
    now = created_at or datetime.now(UTC)
    return RegisteredInstance(
        id=instance_id,
        kind=kind,
        slug=slug or f"{instance_id}-slug",
        name=f"Instance {instance_id}",
        base_url=f"https://{instance_id}.example.com/",
        visibility=visibility,
        owner_id=owner_id,
        tenant_id=tenant_id,
        enabled=enabled,
        is_default=is_default,
        config={"region": "ca-central-1"},
        created_at=now,
        updated_at=now,
        tags=tags or [],
    )


@pytest.mark.asyncio
async def test_list_visible_filters_and_sorts_instances() -> None:
    repo = InMemoryInstanceRepository(
        [
            _instance(
                "tenant-default",
                is_default=True,
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            ),
            _instance("tenant-second", created_at=datetime(2025, 1, 2, tzinfo=UTC)),
            _instance(
                "system-shared",
                visibility=InstanceVisibility.SYSTEM,
                tenant_id=None,
                created_at=datetime(2025, 1, 3, tzinfo=UTC),
            ),
            _instance(
                "user-owned",
                visibility=InstanceVisibility.USER,
                owner_id="user-a",
                tenant_id=None,
                created_at=datetime(2025, 1, 4, tzinfo=UTC),
            ),
            _instance("disabled", enabled=False),
            _instance("other-tenant", tenant_id="tenant-b"),
            _instance("ting", kind=InstanceKind.TING),
        ]
    )
    service = InstanceService(repo)

    visible = await service.list_visible(_principal(), enabled_only=True)

    assert [instance.id for instance in visible] == [
        "tenant-default",
        "system-shared",
        "tenant-second",
        "ting",
        "user-owned",
    ]

    ting_only = await service.list_visible(_principal(), kind=InstanceKind.TING)
    assert [instance.id for instance in ting_only] == ["ting"]


@pytest.mark.asyncio
async def test_get_visible_hides_instances_outside_scope() -> None:
    repo = InMemoryInstanceRepository(
        [
            _instance(
                "mine",
                visibility=InstanceVisibility.USER,
                owner_id="user-a",
                tenant_id=None,
            ),
            _instance(
                "other-user",
                visibility=InstanceVisibility.USER,
                owner_id="user-b",
                tenant_id=None,
            ),
            _instance("other-tenant", tenant_id="tenant-b"),
        ]
    )
    service = InstanceService(repo)

    assert (await service.get_visible(_principal(), "mine")) is not None
    assert await service.get_visible(_principal(), "other-user") is None
    assert await service.get_visible(_principal(), "other-tenant") is None


@pytest.mark.asyncio
async def test_create_instance_normalizes_scope_and_trims_values() -> None:
    repo = InMemoryInstanceRepository()
    service = InstanceService(repo)

    tenant_instance = await service.create_instance(
        _principal(),
        kind=InstanceKind.VOLUNDR,
        slug="  tenant-volundr  ",
        name="  Tenant Volundr  ",
        base_url="https://tenant.example.com///",
        visibility=InstanceVisibility.TENANT,
        config={"mode": "shared"},
    )
    user_instance = await service.create_instance(
        _principal(),
        kind=InstanceKind.TING,
        slug="  user-ting ",
        name=" User Ting ",
        base_url="https://ting.example.com/ ",
        visibility=InstanceVisibility.USER,
    )
    system_instance = await service.create_instance(
        _principal(admin=True),
        kind=InstanceKind.MIMIR,
        slug="system-mimir",
        name="System Mimir",
        base_url="https://mimir.example.com/",
        visibility=InstanceVisibility.SYSTEM,
    )

    assert tenant_instance.slug == "tenant-volundr"
    assert tenant_instance.name == "Tenant Volundr"
    assert tenant_instance.base_url == "https://tenant.example.com"
    assert tenant_instance.owner_id is None
    assert tenant_instance.tenant_id == "tenant-a"
    assert user_instance.owner_id == "user-a"
    assert user_instance.tenant_id is None
    assert system_instance.owner_id is None
    assert system_instance.tenant_id is None


@pytest.mark.asyncio
async def test_create_instance_rejects_invalid_cross_scope_requests() -> None:
    service = InstanceService(InMemoryInstanceRepository())

    with pytest.raises(InstanceAccessError):
        await service.create_instance(
            _principal(),
            kind=InstanceKind.VOLUNDR,
            slug="system",
            name="System",
            base_url="https://system.example.com",
            visibility=InstanceVisibility.SYSTEM,
        )

    with pytest.raises(InstanceAccessError):
        await service.create_instance(
            _principal(),
            kind=InstanceKind.VOLUNDR,
            slug="other-tenant",
            name="Other Tenant",
            base_url="https://tenant-b.example.com",
            visibility=InstanceVisibility.TENANT,
            tenant_id="tenant-b",
        )

    with pytest.raises(InstanceAccessError):
        await service.create_instance(
            _principal(),
            kind=InstanceKind.VOLUNDR,
            slug="other-user",
            name="Other User",
            base_url="https://user-b.example.com",
            visibility=InstanceVisibility.USER,
            owner_id="user-b",
        )

    with pytest.raises(InstanceValidationError):
        await service.create_instance(
            _principal(admin=True),
            kind=InstanceKind.VOLUNDR,
            slug="missing-tenant",
            name="Missing Tenant",
            base_url="https://tenant.example.com",
            visibility=InstanceVisibility.TENANT,
            tenant_id=" ",
        )

    with pytest.raises(InstanceValidationError):
        await service.create_instance(
            _principal(admin=True),
            kind=InstanceKind.VOLUNDR,
            slug="missing-owner",
            name="Missing Owner",
            base_url="https://user.example.com",
            visibility=InstanceVisibility.USER,
            owner_id=" ",
        )


@pytest.mark.asyncio
async def test_update_instance_recomputes_scope_and_preserves_existing_values() -> None:
    existing = _instance(
        "instance-1",
        visibility=InstanceVisibility.TENANT,
        tenant_id="tenant-a",
        enabled=False,
        is_default=False,
    )
    repo = InMemoryInstanceRepository([existing])
    service = InstanceService(repo)

    updated = await service.update_instance(
        _principal(admin=True),
        "instance-1",
        slug="  renamed ",
        name="  Renamed Instance ",
        base_url="https://renamed.example.com///",
        visibility=InstanceVisibility.USER,
        owner_id="owner-2",
        enabled=True,
        is_default=True,
        config={"region": "us-east-1"},
    )

    assert updated.slug == "renamed"
    assert updated.name == "Renamed Instance"
    assert updated.base_url == "https://renamed.example.com"
    assert updated.visibility == InstanceVisibility.USER
    assert updated.owner_id == "owner-2"
    assert updated.tenant_id is None
    assert updated.enabled is True
    assert updated.is_default is True
    assert updated.config == {"region": "us-east-1"}


@pytest.mark.asyncio
async def test_update_and_delete_require_manage_access() -> None:
    managed = _instance(
        "user-owned",
        visibility=InstanceVisibility.USER,
        owner_id="user-a",
        tenant_id=None,
    )
    foreign = _instance(
        "foreign-tenant",
        visibility=InstanceVisibility.TENANT,
        tenant_id="tenant-b",
    )
    repo = InMemoryInstanceRepository([managed, foreign])
    service = InstanceService(repo)

    with pytest.raises(LookupError):
        await service.update_instance(_principal(), "missing", name="missing")

    with pytest.raises(InstanceAccessError):
        await service.update_instance(_principal(), "foreign-tenant", name="forbidden")

    await service.delete_instance(_principal(), "user-owned")
    assert repo.deleted_ids == ["user-owned"]

    with pytest.raises(InstanceAccessError):
        await service.delete_instance(_principal(), "foreign-tenant")

    await service.delete_instance(_principal(), "missing")


@pytest.mark.asyncio
async def test_upsert_seed_instance_updates_existing_match_and_creates_new_seed() -> None:
    existing = _instance(
        "existing-id",
        slug="shared",
        visibility=InstanceVisibility.SYSTEM,
        tenant_id=None,
    )
    repo = InMemoryInstanceRepository([existing])
    service = InstanceService(repo)

    updated = await service.upsert_seed_instance(
        kind=InstanceKind.VOLUNDR,
        slug=" shared ",
        name="Updated Shared",
        base_url="https://updated.example.com/",
        visibility=InstanceVisibility.SYSTEM,
        enabled=False,
        is_default=True,
        config={"region": "eu-west-1"},
    )
    created = await service.upsert_seed_instance(
        kind=InstanceKind.TING,
        slug="ting-seed",
        name="Ting Seed",
        base_url="https://ting.example.com/",
        visibility=InstanceVisibility.TENANT,
        tenant_id="tenant-a",
        instance_id="seed-id",
    )

    assert updated.id == "existing-id"
    assert updated.name == "Updated Shared"
    assert updated.base_url == "https://updated.example.com"
    assert updated.enabled is False
    assert updated.is_default is True
    assert updated.config == {"region": "eu-west-1"}
    assert created.id == "seed-id"
    assert created.tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_seed_configured_instances_skips_incomplete_items(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = InMemoryInstanceRepository()
    service = InstanceService(repo)
    seeded_items = [
        SimpleNamespace(
            id="seed-1",
            kind=InstanceKind.VOLUNDR,
            slug=" seed-one ",
            name=" Seed One ",
            base_url="https://seed-one.example.com/",
            visibility=InstanceVisibility.SYSTEM,
            enabled=True,
            is_default=True,
            config={"mode": "shared"},
        ),
        SimpleNamespace(
            id="seed-2",
            kind=InstanceKind.TING,
            slug=" ",
            name="Incomplete",
            base_url="https://missing.example.com",
            visibility=InstanceVisibility.TENANT,
            tenant_id="tenant-a",
        ),
    ]

    seeded = await seed_configured_instances(service, seeded_items)

    assert seeded == 1
    assert "Skipping incomplete seeded instance" in caplog.text
    assert list(repo.instances) == ["seed-1"]


@pytest.mark.asyncio
async def test_list_visible_filters_by_tags() -> None:
    repo = InMemoryInstanceRepository(
        [
            _instance("gpu-west", tags=["gpu", "us-west"]),
            _instance("gpu-east", tags=["gpu", "us-east"]),
            _instance("cpu-west", tags=["us-west"]),
            _instance("untagged"),
        ]
    )
    service = InstanceService(repo)
    principal = _principal()

    # Default match=all: every selector tag must be present.
    both = await service.list_visible(principal, tags=["gpu", "us-west"])
    assert {i.id for i in both} == {"gpu-west"}

    # match=any: at least one selector tag present.
    either = await service.list_visible(principal, tags=["us-west", "us-east"], match="any")
    assert {i.id for i in either} == {"gpu-west", "gpu-east", "cpu-west"}

    # Empty selector matches everything.
    everything = await service.list_visible(principal)
    assert {i.id for i in everything} == {"gpu-west", "gpu-east", "cpu-west", "untagged"}

    # A selector that nothing satisfies returns nothing (callers fail loud on this).
    none_match = await service.list_visible(principal, tags=["gpu", "us-central"])
    assert none_match == []


@pytest.mark.asyncio
async def test_create_and_update_round_trip_tags() -> None:
    repo = InMemoryInstanceRepository()
    service = InstanceService(repo)
    admin = _principal(admin=True)

    created = await service.create_instance(
        admin,
        kind=InstanceKind.VOLUNDR,
        slug="tagged",
        name="Tagged",
        base_url="https://tagged.example.com",
        visibility=InstanceVisibility.SYSTEM,
        tags=["gpu", "prod"],
    )
    assert created.tags == ["gpu", "prod"]

    updated = await service.update_instance(admin, created.id, tags=["cpu"])
    assert updated.tags == ["cpu"]

    # Omitting tags on update leaves them unchanged.
    unchanged = await service.update_instance(admin, created.id, name="Renamed")
    assert unchanged.tags == ["cpu"]
