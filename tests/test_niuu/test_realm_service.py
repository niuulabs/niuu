"""Unit tests for RealmService against an in-memory RealmRepository."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from niuu.domain.models import Capability, Realm, TrustGrant
from niuu.domain.services.realm import RealmNotFoundError, RealmService
from niuu.ports.realm_repository import RealmRepository

TENANT = "tenant-1"


class InMemoryRealmRepository(RealmRepository):
    """In-memory fake for testing the service without a database."""

    def __init__(self) -> None:
        self.realms: dict[UUID, Realm] = {}
        self.grants: dict[UUID, TrustGrant] = {}
        self.capabilities: dict[UUID, Capability] = {}

    async def list_realms(self, tenant_id: str) -> list[Realm]:
        return [realm for realm in self.realms.values() if realm.tenant_id == tenant_id]

    async def get_realm(self, realm_id: UUID) -> Realm | None:
        return self.realms.get(realm_id)

    async def get_tenant_realm(self, realm_ref: UUID | str, tenant_id: str) -> Realm | None:
        for realm in self.realms.values():
            if realm.tenant_id == tenant_id and realm_ref in (realm.id, realm.slug):
                return realm
        return None

    async def save_realm(self, realm: Realm) -> Realm:
        self.realms[realm.id] = realm
        return realm

    async def delete_realm(self, realm_id: UUID) -> None:
        self.realms.pop(realm_id, None)
        self.grants = {k: g for k, g in self.grants.items() if g.realm_id != realm_id}
        self.capabilities = {k: c for k, c in self.capabilities.items() if c.realm_id != realm_id}

    async def list_trust_grants(self, realm_id: UUID) -> list[TrustGrant]:
        return [g for g in self.grants.values() if g.realm_id == realm_id]

    async def save_trust_grant(self, grant: TrustGrant) -> TrustGrant:
        self.grants[grant.id] = grant
        return grant

    async def list_capabilities(self, realm_id: UUID) -> list[Capability]:
        return [c for c in self.capabilities.values() if c.realm_id == realm_id]

    async def save_capability(self, capability: Capability) -> Capability:
        self.capabilities[capability.id] = capability
        return capability

    async def upsert_capability(self, capability: Capability) -> Capability:
        for existing in self.capabilities.values():
            if existing.realm_id == capability.realm_id and existing.name == capability.name:
                merged = Capability(
                    id=existing.id,
                    realm_id=capability.realm_id,
                    name=capability.name,
                    kind=capability.kind,
                    status=capability.status,
                    trust_level=capability.trust_level,
                    mimir_page_path=capability.mimir_page_path,
                    notes=capability.notes,
                    created_at=existing.created_at,
                    updated_at=capability.updated_at,
                )
                self.capabilities[existing.id] = merged
                return merged
        self.capabilities[capability.id] = capability
        return capability


@pytest.fixture
def service() -> RealmService:
    return RealmService(InMemoryRealmRepository())


@pytest.mark.asyncio
async def test_delete_realm_removes_realm_grants_and_capabilities(
    service: RealmService,
) -> None:
    realm = await service.create_realm("forge", "Forge", tenant_id=TENANT)
    await service.grant_trust(realm.id, "build", level=2)
    await service.record_capability(realm.id, "grep-tool", "tool")

    deleted = await service.delete_realm("forge", TENANT)

    assert deleted is not None and deleted.id == realm.id
    assert await service.get_realm("forge", TENANT) is None
    assert await service.list_trust_grants(realm.id) == []
    assert await service.list_capabilities(realm.id) == []


@pytest.mark.asyncio
async def test_delete_realm_unknown_returns_none(service: RealmService) -> None:
    assert await service.delete_realm("ghost", TENANT) is None
    assert await service.delete_realm(uuid4(), TENANT) is None


# ---------------------------------------------------------------------------
# create_realm / get_realm / list_realms
# ---------------------------------------------------------------------------


async def test_create_realm_persists_and_defaults(service: RealmService) -> None:
    realm = await service.create_realm(slug="odin-forge", name="Odin Forge", tenant_id=TENANT)

    assert isinstance(realm.id, UUID)
    assert realm.slug == "odin-forge"
    assert realm.name == "Odin Forge"
    assert realm.autonomy_profile == "balanced"
    assert realm.sleipnir_domain is None
    assert realm.created_at == realm.updated_at


async def test_create_realm_with_all_fields(service: RealmService) -> None:
    realm = await service.create_realm(
        slug="raiders",
        name="Raiders",
        tenant_id=TENANT,
        sleipnir_domain="raiders.mesh",
        owner_id="user-1",
        instance_id="inst-9",
        autonomy_profile="autonomous",
    )

    assert realm.sleipnir_domain == "raiders.mesh"
    assert realm.owner_id == "user-1"
    assert realm.instance_id == "inst-9"
    assert realm.autonomy_profile == "autonomous"


async def test_upsert_realm_creates_at_the_given_id(service: RealmService) -> None:
    realm_id = uuid4()

    realm = await service.upsert_realm(realm_id, slug="workshop", name="Workshop", tenant_id=TENANT)

    assert realm.id == realm_id
    assert realm.slug == "workshop"
    assert realm.created_at == realm.updated_at
    assert await service.get_realm(realm_id, TENANT) is not None


async def test_upsert_realm_replaces_an_existing_row_without_changing_created_at(
    service: RealmService,
) -> None:
    created = await service.upsert_realm(
        uuid4(), slug="workshop", name="Workshop", tenant_id=TENANT
    )

    updated = await service.upsert_realm(
        created.id,
        slug="workshop",
        name="Workshop Renamed",
        tenant_id=TENANT,
        owner_id="user-9",
    )

    assert updated.id == created.id
    assert updated.name == "Workshop Renamed"
    assert updated.owner_id == "user-9"
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at


async def test_get_realm_by_slug(service: RealmService) -> None:
    created = await service.create_realm(slug="scouts", name="Scouts", tenant_id=TENANT)

    fetched = await service.get_realm("scouts", TENANT)

    assert fetched is not None
    assert fetched.id == created.id


async def test_get_realm_by_id(service: RealmService) -> None:
    created = await service.create_realm(slug="scouts", name="Scouts", tenant_id=TENANT)

    fetched = await service.get_realm(created.id, TENANT)

    assert fetched is not None
    assert fetched.slug == "scouts"


async def test_get_realm_unknown_returns_none(service: RealmService) -> None:
    assert await service.get_realm("nope", TENANT) is None


async def test_list_realms(service: RealmService) -> None:
    await service.create_realm(slug="a", name="A", tenant_id=TENANT)
    await service.create_realm(slug="b", name="B", tenant_id=TENANT)

    realms = await service.list_realms(TENANT)

    assert {r.slug for r in realms} == {"a", "b"}


# ---------------------------------------------------------------------------
# grant_trust / list_trust_grants
# ---------------------------------------------------------------------------


async def test_grant_trust_defaults(service: RealmService) -> None:
    realm = await service.create_realm(slug="r", name="R", tenant_id=TENANT)

    grant = await service.grant_trust(realm.id, "observe")

    assert grant.action_class == "observe"
    assert grant.target == "*"
    assert grant.level == 0
    assert grant.limits == {}
    assert grant.granted_by is None


async def test_grant_trust_with_limits(service: RealmService) -> None:
    realm = await service.create_realm(slug="r", name="R", tenant_id=TENANT)

    grant = await service.grant_trust(
        realm.id,
        "build",
        target="tools",
        level=2,
        limits={"workflow": "tool-builder"},
        granted_by="admin",
    )

    assert grant.level == 2
    assert grant.limits == {"workflow": "tool-builder"}
    assert grant.granted_by == "admin"


async def test_list_trust_grants_scoped_to_realm(service: RealmService) -> None:
    realm_a = await service.create_realm(slug="a", name="A", tenant_id=TENANT)
    realm_b = await service.create_realm(slug="b", name="B", tenant_id=TENANT)
    await service.grant_trust(realm_a.id, "observe")
    await service.grant_trust(realm_b.id, "build")

    grants_a = await service.list_trust_grants(realm_a.id)

    assert len(grants_a) == 1
    assert grants_a[0].action_class == "observe"


# ---------------------------------------------------------------------------
# record_capability / list_capabilities
# ---------------------------------------------------------------------------


async def test_record_capability_defaults(service: RealmService) -> None:
    realm = await service.create_realm(slug="r", name="R", tenant_id=TENANT)

    cap = await service.record_capability(realm.id, "grep-tool", "tool")

    assert cap.name == "grep-tool"
    assert cap.kind == "tool"
    assert cap.status == "gap"
    assert cap.trust_level == 0


async def test_record_capability_upserts_by_name(service: RealmService) -> None:
    realm = await service.create_realm(slug="r", name="R", tenant_id=TENANT)
    first = await service.record_capability(realm.id, "grep-tool", "tool", status="gap")

    second = await service.record_capability(realm.id, "grep-tool", "tool", status="building")

    caps = await service.list_capabilities(realm.id)
    assert len(caps) == 1
    assert second.id == first.id
    assert caps[0].status == "building"


async def test_list_capabilities_scoped_to_realm(service: RealmService) -> None:
    realm_a = await service.create_realm(slug="a", name="A", tenant_id=TENANT)
    realm_b = await service.create_realm(slug="b", name="B", tenant_id=TENANT)
    await service.record_capability(realm_a.id, "cap-a", "tool")
    await service.record_capability(realm_b.id, "cap-b", "skill")

    caps_a = await service.list_capabilities(realm_a.id)

    assert len(caps_a) == 1
    assert caps_a[0].name == "cap-a"


# ---------------------------------------------------------------------------
# resolve_build_grant — the load-bearing method for P3/P4
# ---------------------------------------------------------------------------


async def test_resolve_build_grant_returns_build_grant(service: RealmService) -> None:
    realm = await service.create_realm(slug="forge", name="Forge", tenant_id=TENANT)
    await service.grant_trust(realm.id, "observe", level=1)
    await service.grant_trust(realm.id, "build", level=3, limits={"workflow": "tool-builder"})

    grant = await service.resolve_build_grant("forge", TENANT)

    assert grant is not None
    assert grant.action_class == "build"
    assert grant.level == 3
    assert grant.limits == {"workflow": "tool-builder"}


async def test_resolve_build_grant_picks_most_recent(service: RealmService) -> None:
    # Append-only ledger: the latest grant is the operator's standing decision,
    # even when it demotes the level — trust must be revocable.
    realm = await service.create_realm(slug="forge", name="Forge", tenant_id=TENANT)
    await service.grant_trust(realm.id, "build", level=1)
    await service.grant_trust(realm.id, "build", level=5)
    await service.grant_trust(realm.id, "build", level=2)

    grant = await service.resolve_build_grant("forge", TENANT)

    assert grant is not None
    assert grant.level == 2


async def test_resolve_build_grant_by_realm_id(service: RealmService) -> None:
    realm = await service.create_realm(slug="forge", name="Forge", tenant_id=TENANT)
    await service.grant_trust(realm.id, "build", level=1)

    grant = await service.resolve_build_grant(realm.id, TENANT)

    assert grant is not None
    assert grant.action_class == "build"


async def test_resolve_build_grant_unknown_realm_returns_none(
    service: RealmService,
) -> None:
    assert await service.resolve_build_grant("ghost", TENANT) is None


async def test_resolve_build_grant_no_build_grant_returns_none(
    service: RealmService,
) -> None:
    realm = await service.create_realm(slug="forge", name="Forge", tenant_id=TENANT)
    await service.grant_trust(realm.id, "observe", level=9)

    assert await service.resolve_build_grant("forge", TENANT) is None


async def test_resolve_build_grant_unknown_uuid_returns_none(
    service: RealmService,
) -> None:
    assert await service.resolve_build_grant(uuid4(), TENANT) is None


# ---------------------------------------------------------------------------
# Tenant boundaries
# ---------------------------------------------------------------------------


async def test_realms_are_invisible_to_other_tenants(service: RealmService) -> None:
    realm = await service.create_realm("forge", "Forge", tenant_id="tenant-a")

    assert await service.get_realm("forge", "tenant-b") is None
    assert await service.get_realm(realm.id, "tenant-b") is None
    assert await service.list_realms("tenant-b") == []


async def test_two_tenants_can_use_the_same_slug(service: RealmService) -> None:
    first = await service.create_realm("forge", "Forge A", tenant_id="tenant-a")
    second = await service.create_realm("forge", "Forge B", tenant_id="tenant-b")

    fetched_first = await service.get_realm("forge", "tenant-a")
    fetched_second = await service.get_realm("forge", "tenant-b")

    assert fetched_first is not None and fetched_first.id == first.id
    assert fetched_second is not None and fetched_second.id == second.id


async def test_delete_realm_cannot_reach_another_tenant(service: RealmService) -> None:
    realm = await service.create_realm("forge", "Forge", tenant_id="tenant-a")

    assert await service.delete_realm(realm.id, "tenant-b") is None
    assert await service.get_realm(realm.id, "tenant-a") is not None


async def test_resolve_build_grant_cannot_reach_another_tenant(service: RealmService) -> None:
    realm = await service.create_realm("forge", "Forge", tenant_id="tenant-a")
    await service.grant_trust(realm.id, "build", level=3)

    assert await service.resolve_build_grant("forge", "tenant-b") is None


async def test_upsert_realm_refuses_an_id_in_another_tenant(service: RealmService) -> None:
    realm = await service.create_realm("forge", "Forge", tenant_id="tenant-a")

    with pytest.raises(RealmNotFoundError):
        await service.upsert_realm(realm.id, slug="forge", name="Taken", tenant_id="tenant-b")

    stored = await service.get_realm(realm.id, "tenant-a")
    assert stored is not None and stored.name == "Forge"


async def test_a_sync_that_names_no_project_keeps_the_realms_project() -> None:
    service = RealmService(InMemoryRealmRepository())
    project_id = uuid4()
    realm = await service.create_realm("copy", "Copy", tenant_id="t", project_id=project_id)

    synced = await service.upsert_realm(realm.id, "copy", "Copy", tenant_id="t")

    assert synced.project_id == project_id


async def test_set_project_moves_only_the_tenants_realm() -> None:
    service = RealmService(InMemoryRealmRepository())
    await service.create_realm("copy", "Copy", tenant_id="t")
    project_id = uuid4()

    moved = await service.set_project("copy", "t", project_id)

    assert moved.project_id == project_id
    with pytest.raises(RealmNotFoundError):
        await service.set_project("copy", "other", project_id)
