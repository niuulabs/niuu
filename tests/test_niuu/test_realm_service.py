"""Unit tests for RealmService against an in-memory RealmRepository."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from niuu.domain.models import Capability, Realm, TrustGrant
from niuu.domain.services.realm import RealmService
from niuu.ports.realm_repository import RealmRepository


class InMemoryRealmRepository(RealmRepository):
    """In-memory fake for testing the service without a database."""

    def __init__(self) -> None:
        self.realms: dict[UUID, Realm] = {}
        self.grants: dict[UUID, TrustGrant] = {}
        self.capabilities: dict[UUID, Capability] = {}

    async def list_realms(self) -> list[Realm]:
        return list(self.realms.values())

    async def get_realm(self, realm_ref: UUID | str) -> Realm | None:
        if isinstance(realm_ref, UUID):
            return self.realms.get(realm_ref)
        for realm in self.realms.values():
            if realm.slug == realm_ref:
                return realm
        return None

    async def save_realm(self, realm: Realm) -> Realm:
        self.realms[realm.id] = realm
        return realm

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


# ---------------------------------------------------------------------------
# create_realm / get_realm / list_realms
# ---------------------------------------------------------------------------


async def test_create_realm_persists_and_defaults(service: RealmService) -> None:
    realm = await service.create_realm(slug="odin-forge", name="Odin Forge")

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
        sleipnir_domain="raiders.mesh",
        owner_id="user-1",
        instance_id="inst-9",
        autonomy_profile="autonomous",
    )

    assert realm.sleipnir_domain == "raiders.mesh"
    assert realm.owner_id == "user-1"
    assert realm.instance_id == "inst-9"
    assert realm.autonomy_profile == "autonomous"


async def test_get_realm_by_slug(service: RealmService) -> None:
    created = await service.create_realm(slug="scouts", name="Scouts")

    fetched = await service.get_realm("scouts")

    assert fetched is not None
    assert fetched.id == created.id


async def test_get_realm_by_id(service: RealmService) -> None:
    created = await service.create_realm(slug="scouts", name="Scouts")

    fetched = await service.get_realm(created.id)

    assert fetched is not None
    assert fetched.slug == "scouts"


async def test_get_realm_unknown_returns_none(service: RealmService) -> None:
    assert await service.get_realm("nope") is None


async def test_list_realms(service: RealmService) -> None:
    await service.create_realm(slug="a", name="A")
    await service.create_realm(slug="b", name="B")

    realms = await service.list_realms()

    assert {r.slug for r in realms} == {"a", "b"}


# ---------------------------------------------------------------------------
# grant_trust / list_trust_grants
# ---------------------------------------------------------------------------


async def test_grant_trust_defaults(service: RealmService) -> None:
    realm = await service.create_realm(slug="r", name="R")

    grant = await service.grant_trust(realm.id, "observe")

    assert grant.action_class == "observe"
    assert grant.target == "*"
    assert grant.level == 0
    assert grant.limits == {}
    assert grant.granted_by is None


async def test_grant_trust_with_limits(service: RealmService) -> None:
    realm = await service.create_realm(slug="r", name="R")

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
    realm_a = await service.create_realm(slug="a", name="A")
    realm_b = await service.create_realm(slug="b", name="B")
    await service.grant_trust(realm_a.id, "observe")
    await service.grant_trust(realm_b.id, "build")

    grants_a = await service.list_trust_grants(realm_a.id)

    assert len(grants_a) == 1
    assert grants_a[0].action_class == "observe"


# ---------------------------------------------------------------------------
# record_capability / list_capabilities
# ---------------------------------------------------------------------------


async def test_record_capability_defaults(service: RealmService) -> None:
    realm = await service.create_realm(slug="r", name="R")

    cap = await service.record_capability(realm.id, "grep-tool", "tool")

    assert cap.name == "grep-tool"
    assert cap.kind == "tool"
    assert cap.status == "gap"
    assert cap.trust_level == 0


async def test_record_capability_upserts_by_name(service: RealmService) -> None:
    realm = await service.create_realm(slug="r", name="R")
    first = await service.record_capability(realm.id, "grep-tool", "tool", status="gap")

    second = await service.record_capability(
        realm.id, "grep-tool", "tool", status="building"
    )

    caps = await service.list_capabilities(realm.id)
    assert len(caps) == 1
    assert second.id == first.id
    assert caps[0].status == "building"


async def test_list_capabilities_scoped_to_realm(service: RealmService) -> None:
    realm_a = await service.create_realm(slug="a", name="A")
    realm_b = await service.create_realm(slug="b", name="B")
    await service.record_capability(realm_a.id, "cap-a", "tool")
    await service.record_capability(realm_b.id, "cap-b", "skill")

    caps_a = await service.list_capabilities(realm_a.id)

    assert len(caps_a) == 1
    assert caps_a[0].name == "cap-a"


# ---------------------------------------------------------------------------
# resolve_build_grant — the load-bearing method for P3/P4
# ---------------------------------------------------------------------------


async def test_resolve_build_grant_returns_build_grant(service: RealmService) -> None:
    realm = await service.create_realm(slug="forge", name="Forge")
    await service.grant_trust(realm.id, "observe", level=1)
    await service.grant_trust(
        realm.id, "build", level=3, limits={"workflow": "tool-builder"}
    )

    grant = await service.resolve_build_grant("forge")

    assert grant is not None
    assert grant.action_class == "build"
    assert grant.level == 3
    assert grant.limits == {"workflow": "tool-builder"}


async def test_resolve_build_grant_picks_highest_level(service: RealmService) -> None:
    realm = await service.create_realm(slug="forge", name="Forge")
    await service.grant_trust(realm.id, "build", level=1)
    await service.grant_trust(realm.id, "build", level=5)
    await service.grant_trust(realm.id, "build", level=2)

    grant = await service.resolve_build_grant("forge")

    assert grant is not None
    assert grant.level == 5


async def test_resolve_build_grant_by_realm_id(service: RealmService) -> None:
    realm = await service.create_realm(slug="forge", name="Forge")
    await service.grant_trust(realm.id, "build", level=1)

    grant = await service.resolve_build_grant(realm.id)

    assert grant is not None
    assert grant.action_class == "build"


async def test_resolve_build_grant_unknown_realm_returns_none(
    service: RealmService,
) -> None:
    assert await service.resolve_build_grant("ghost") is None


async def test_resolve_build_grant_no_build_grant_returns_none(
    service: RealmService,
) -> None:
    realm = await service.create_realm(slug="forge", name="Forge")
    await service.grant_trust(realm.id, "observe", level=9)

    assert await service.resolve_build_grant("forge") is None


async def test_resolve_build_grant_unknown_uuid_returns_none(
    service: RealmService,
) -> None:
    assert await service.resolve_build_grant(uuid4()) is None
