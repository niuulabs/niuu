"""Tests for the shared instance registry service."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.services.instances import (
    InstanceAccessError,
    InstanceService,
    InstanceTransportSecurityError,
    InstanceValidationError,
)
from niuu.domain.transport_security import (
    _DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES,
    configure_trusted_plaintext_host_suffixes,
)
from niuu.service_instances import seed_configured_instances

_VALID_FINGERPRINT = "ab" * 32


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
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())

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
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())

    assert (await service.get_visible(_principal(), "mine")) is not None
    assert await service.get_visible(_principal(), "other-user") is None
    assert await service.get_visible(_principal(), "other-tenant") is None


@pytest.mark.asyncio
async def test_create_instance_normalizes_scope_and_trims_values() -> None:
    repo = InMemoryInstanceRepository()
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())

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
    assert user_instance.tenant_id == "tenant-a"
    assert system_instance.owner_id is None
    assert system_instance.tenant_id is None


@pytest.mark.asyncio
async def test_create_instance_rejects_invalid_cross_scope_requests() -> None:
    service = InstanceService(
        InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
    )

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
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())

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
    assert updated.tenant_id == "tenant-a"
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
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())

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
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())

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
async def test_seed_configured_instances_raises_on_incomplete_items() -> None:
    """An incomplete `niuu.instances` entry is a configuration error, not a
    hint to skip — it must stop startup with the remedy in the message
    rather than silently registering fewer instances than configured."""
    repo = InMemoryInstanceRepository()
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
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

    with pytest.raises(InstanceValidationError, match="niuu.instances\\[1\\] is incomplete"):
        await seed_configured_instances(service, seeded_items)

    # The first, valid entry was already persisted before the second one
    # was found incomplete — a partial seed on a hard failure, not a
    # silently-smaller one.
    assert list(repo.instances) == ["seed-1"]


@pytest.mark.asyncio
async def test_seed_configured_instances_persists_every_complete_item() -> None:
    repo = InMemoryInstanceRepository()
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
    seeded_items = [
        SimpleNamespace(
            id="seed-1",
            kind=InstanceKind.VOLUNDR,
            slug="seed-one",
            name="Seed One",
            base_url="https://seed-one.example.com",
            visibility=InstanceVisibility.SYSTEM,
        ),
    ]

    seeded = await seed_configured_instances(service, seeded_items)

    assert seeded == 1
    assert list(repo.instances) == ["seed-1"]


@pytest.mark.asyncio
async def test_seed_configured_instances_accepts_ymir_style_in_cluster_seeds_by_default() -> None:
    """The real startup path (niuu.service_instances.seed_configured_instances,
    called from the Guild composition root) must not refuse a live cluster's
    plain-http in-cluster seeds with the default trusted-plaintext suffixes —
    this is exactly the deployment-safety scenario the suffix exemption
    exists for."""
    repo = InMemoryInstanceRepository()
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
    seeded_items = [
        SimpleNamespace(
            id="volundr",
            kind=InstanceKind.VOLUNDR,
            slug="volundr",
            name="Volundr",
            base_url="http://niuu-volundr.volundr.svc.cluster.local",
            visibility=InstanceVisibility.SYSTEM,
            enabled=True,
            is_default=True,
            config={"ravn_base_url": "http://niuu-ravn.volundr.svc.cluster.local"},
        ),
    ]

    seeded = await seed_configured_instances(service, seeded_items)

    assert seeded == 1
    assert repo.instances["volundr"].base_url == "http://niuu-volundr.volundr.svc.cluster.local"


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
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
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
    service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
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


async def test_cedar_prevents_cross_tenant_admin_registry_changes():
    from identity.adapters.cedar import CedarAuthorizationAdapter

    repo = InMemoryInstanceRepository()
    service = InstanceService(repo, authorization=CedarAuthorizationAdapter())
    alice = Principal("alice", "", "acme", ["volundr:developer"])
    foreign_admin = Principal("bob", "", "other", ["volundr:admin"])
    instance = await service.create_instance(
        alice,
        kind=InstanceKind.TING,
        slug="personal",
        name="Personal",
        base_url="https://ting.test",
        visibility=InstanceVisibility.USER,
    )
    assert instance.tenant_id == "acme"
    assert await service.get_visible(foreign_admin, instance.id) is None
    with pytest.raises(InstanceAccessError):
        await service.update_instance(foreign_admin, instance.id, name="hijacked")
    with pytest.raises(InstanceAccessError):
        await service.delete_instance(foreign_admin, instance.id)


class TestTransportSecurity:
    """A remote Guild instance must use https:// unless it explicitly opts
    into plaintext, and a configured tls_fingerprint must be well-formed and
    only ever paired with https:// — see .claude/rules/no-fallbacks.md and
    the LAN transport owner decision (Tailscale/pinned-TLS first, plaintext
    only as an explicit per-instance opt-in)."""

    @pytest.mark.asyncio
    async def test_create_instance_rejects_plain_http_without_opt_in(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        with pytest.raises(InstanceTransportSecurityError, match="allow_plaintext"):
            await service.create_instance(
                _principal(admin=True),
                kind=InstanceKind.VOLUNDR,
                slug="plain",
                name="Plain",
                base_url="http://remote.example.com",
                visibility=InstanceVisibility.SYSTEM,
            )

    @pytest.mark.asyncio
    async def test_create_instance_allows_plain_http_with_explicit_opt_in(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        instance = await service.create_instance(
            _principal(admin=True),
            kind=InstanceKind.VOLUNDR,
            slug="tailnet",
            name="Tailnet",
            base_url="http://100.90.20.64:8080",
            visibility=InstanceVisibility.SYSTEM,
            config={"allow_plaintext": True},
        )
        assert instance.base_url == "http://100.90.20.64:8080"
        assert instance.config["allow_plaintext"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("host", "url_host"),
        [("localhost", "localhost"), ("127.0.0.1", "127.0.0.1"), ("::1", "[::1]")],
    )
    async def test_create_instance_exempts_localhost_from_https_requirement(
        self, host: str, url_host: str
    ) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        instance = await service.create_instance(
            _principal(admin=True),
            kind=InstanceKind.VOLUNDR,
            slug=f"local-{host.replace(':', '')}",
            name="Local",
            base_url=f"http://{url_host}:8080",
            visibility=InstanceVisibility.SYSTEM,
        )
        assert instance.base_url == f"http://{url_host}:8080"

    @pytest.mark.asyncio
    async def test_create_instance_exempts_embedded_transport(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        instance = await service.create_instance(
            _principal(admin=True),
            kind=InstanceKind.VOLUNDR,
            slug="local-forge",
            name="Local Forge",
            base_url="embedded://local-forge",
            visibility=InstanceVisibility.SYSTEM,
            config={"transport": "embedded"},
        )
        assert instance.base_url == "embedded://local-forge"

    @pytest.mark.asyncio
    async def test_update_instance_rejects_downgrade_to_plain_http(self) -> None:
        repo = InMemoryInstanceRepository()
        service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
        admin = _principal(admin=True)
        instance = await service.create_instance(
            admin,
            kind=InstanceKind.VOLUNDR,
            slug="secure",
            name="Secure",
            base_url="https://secure.example.com",
            visibility=InstanceVisibility.SYSTEM,
        )
        with pytest.raises(InstanceTransportSecurityError):
            await service.update_instance(admin, instance.id, base_url="http://secure.example.com")

    @pytest.mark.asyncio
    async def test_create_instance_accepts_a_well_formed_tls_fingerprint(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        instance = await service.create_instance(
            _principal(admin=True),
            kind=InstanceKind.VOLUNDR,
            slug="pinned",
            name="Pinned",
            base_url="https://pinned.example.com",
            visibility=InstanceVisibility.SYSTEM,
            config={"tls_fingerprint": _VALID_FINGERPRINT},
        )
        assert instance.config["tls_fingerprint"] == _VALID_FINGERPRINT

    @pytest.mark.asyncio
    async def test_create_instance_rejects_a_malformed_tls_fingerprint(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        with pytest.raises(InstanceTransportSecurityError, match="sha256 hex digest"):
            await service.create_instance(
                _principal(admin=True),
                kind=InstanceKind.VOLUNDR,
                slug="bad-pin",
                name="Bad Pin",
                base_url="https://bad-pin.example.com",
                visibility=InstanceVisibility.SYSTEM,
                config={"tls_fingerprint": "not-a-fingerprint"},
            )

    @pytest.mark.asyncio
    async def test_create_instance_rejects_a_tls_fingerprint_on_plain_http(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        with pytest.raises(InstanceTransportSecurityError, match="https"):
            await service.create_instance(
                _principal(admin=True),
                kind=InstanceKind.VOLUNDR,
                slug="pin-over-http",
                name="Pin Over HTTP",
                base_url="http://100.90.20.64:8080",
                visibility=InstanceVisibility.SYSTEM,
                config={"allow_plaintext": True, "tls_fingerprint": _VALID_FINGERPRINT},
            )

    @pytest.mark.asyncio
    async def test_upsert_seed_instance_rejects_plain_http_without_opt_in(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        with pytest.raises(InstanceTransportSecurityError):
            await service.upsert_seed_instance(
                kind=InstanceKind.VOLUNDR,
                slug="seeded",
                name="Seeded",
                base_url="http://seeded.example.com",
                visibility=InstanceVisibility.SYSTEM,
            )

    @pytest.mark.asyncio
    async def test_upsert_seed_instance_allows_a_ymir_style_in_cluster_seed_by_default(
        self,
    ) -> None:
        """A live cluster's Guild seed, like ymir's, registers
        http://niuu-volundr.volundr.svc.cluster.local with a
        ravn_base_url on the same in-cluster suffix — both must pass without
        an explicit allow_plaintext, or every such deployment's Guild would
        refuse to start."""
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        instance = await service.upsert_seed_instance(
            kind=InstanceKind.VOLUNDR,
            slug="volundr",
            name="Volundr",
            base_url="http://niuu-volundr.volundr.svc.cluster.local",
            visibility=InstanceVisibility.SYSTEM,
            config={"ravn_base_url": "http://niuu-ravn.volundr.svc.cluster.local"},
        )
        assert instance.base_url == "http://niuu-volundr.volundr.svc.cluster.local"
        assert instance.config["ravn_base_url"] == "http://niuu-ravn.volundr.svc.cluster.local"

    @pytest.mark.asyncio
    async def test_upsert_seed_instance_refuses_the_in_cluster_seed_once_emptied(self) -> None:
        """guild_transport_trusted_plaintext_host_suffixes: [] is an
        operator decision to require the explicit opt-in everywhere,
        in-cluster addresses included — the suffix exemption must actually
        be gone, not just unused."""
        configure_trusted_plaintext_host_suffixes([])
        try:
            service = InstanceService(
                InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
            )
            with pytest.raises(InstanceTransportSecurityError, match="allow_plaintext"):
                await service.upsert_seed_instance(
                    kind=InstanceKind.VOLUNDR,
                    slug="volundr",
                    name="Volundr",
                    base_url="http://niuu-volundr.volundr.svc.cluster.local",
                    visibility=InstanceVisibility.SYSTEM,
                )
        finally:
            configure_trusted_plaintext_host_suffixes(_DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES)

    @pytest.mark.asyncio
    async def test_create_instance_rejects_an_insecure_ravn_base_url_even_when_base_url_is_https(
        self,
    ) -> None:
        """A split-service target's ravn_base_url is what a caller actually
        dials for Ravn reads (see rest_ravn._ravn_base_url) — an insecure
        one must be caught here just like an insecure base_url, even though
        base_url itself is fine."""
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        with pytest.raises(InstanceTransportSecurityError, match="allow_plaintext"):
            await service.create_instance(
                _principal(admin=True),
                kind=InstanceKind.VOLUNDR,
                slug="split",
                name="Split",
                base_url="https://volundr.example.com",
                visibility=InstanceVisibility.SYSTEM,
                config={"ravn_base_url": "http://ravn.example.com"},
            )

    @pytest.mark.asyncio
    async def test_create_instance_allows_an_insecure_ravn_base_url_with_opt_in(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        instance = await service.create_instance(
            _principal(admin=True),
            kind=InstanceKind.VOLUNDR,
            slug="split",
            name="Split",
            base_url="https://volundr.example.com",
            visibility=InstanceVisibility.SYSTEM,
            config={"ravn_base_url": "http://ravn.example.com", "allow_plaintext": True},
        )
        assert instance.config["ravn_base_url"] == "http://ravn.example.com"

    @pytest.mark.asyncio
    async def test_create_instance_rejects_a_non_boolean_allow_plaintext(self) -> None:
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        with pytest.raises(InstanceTransportSecurityError, match="boolean"):
            await service.create_instance(
                _principal(admin=True),
                kind=InstanceKind.VOLUNDR,
                slug="bad-flag",
                name="Bad Flag",
                base_url="http://100.90.20.64:8080",
                visibility=InstanceVisibility.SYSTEM,
                config={"allow_plaintext": "true"},
            )

    @pytest.mark.asyncio
    async def test_create_instance_does_not_exempt_a_plain_http_url_flagged_as_embedded(
        self,
    ) -> None:
        """The embedded exemption is keyed on the base_url's own scheme
        (embedded://), never on the user-editable config.transport flag — a
        caller dialling base_url directly (bypassing whatever routing reads
        that flag) must get the same answer everyone else gets."""
        service = InstanceService(
            InMemoryInstanceRepository(), authorization=AllowAllAuthorizationAdapter()
        )
        with pytest.raises(InstanceTransportSecurityError, match="allow_plaintext"):
            await service.create_instance(
                _principal(admin=True),
                kind=InstanceKind.VOLUNDR,
                slug="fake-embedded",
                name="Fake Embedded",
                base_url="http://not-actually-embedded.example.com",
                visibility=InstanceVisibility.SYSTEM,
                config={"transport": "embedded"},
            )

    @pytest.mark.asyncio
    async def test_update_instance_allows_disabling_a_legacy_insecure_row(self) -> None:
        """A row that predates this validation (inserted directly into the
        repository here, simulating one already in the database) must still
        be disable-able — the write-time check must not trap an operator
        trying to turn the insecure instance off. Call-time enforcement in
        guild_transport.py remains the real boundary regardless."""
        repo = InMemoryInstanceRepository()
        service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
        admin = _principal(admin=True)
        legacy = _instance("legacy", visibility=InstanceVisibility.SYSTEM, tenant_id=None)
        legacy = replace(legacy, base_url="http://legacy.example.com", config={})
        repo.instances[legacy.id] = legacy

        updated = await service.update_instance(admin, legacy.id, enabled=False)

        assert updated.enabled is False
        assert updated.base_url == "http://legacy.example.com"

    @pytest.mark.asyncio
    async def test_update_instance_allows_untouched_transport_fields_on_a_legacy_insecure_row(
        self,
    ) -> None:
        repo = InMemoryInstanceRepository()
        service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
        admin = _principal(admin=True)
        legacy = _instance("legacy", visibility=InstanceVisibility.SYSTEM, tenant_id=None)
        legacy = replace(legacy, base_url="http://legacy.example.com", config={})
        repo.instances[legacy.id] = legacy

        updated = await service.update_instance(admin, legacy.id, name="Renamed Legacy")

        assert updated.name == "Renamed Legacy"
        assert updated.base_url == "http://legacy.example.com"

    @pytest.mark.asyncio
    async def test_update_instance_revalidates_when_re_enabling_an_insecure_row(self) -> None:
        """PATCH {base_url: 'http://x', enabled: false} correctly disables
        without a 422 (see test above). A later, separate
        PATCH {enabled: true} that never touches base_url/config must not
        silently re-enable that same insecure row — it has to re-run the
        transport check, exactly as if the insecure base_url were being set
        for the first time."""
        repo = InMemoryInstanceRepository()
        service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
        admin = _principal(admin=True)
        legacy = _instance("legacy", visibility=InstanceVisibility.SYSTEM, tenant_id=None)
        legacy = replace(legacy, base_url="http://legacy.example.com", config={}, enabled=False)
        repo.instances[legacy.id] = legacy

        with pytest.raises(InstanceTransportSecurityError, match="allow_plaintext"):
            await service.update_instance(admin, legacy.id, enabled=True)

    @pytest.mark.asyncio
    async def test_update_instance_allows_re_enabling_once_made_secure(self) -> None:
        """The re-enable check is satisfied once the row is actually secure
        (or has opted into plaintext) — it is not a blanket ban on
        re-enabling a previously-disabled instance."""
        repo = InMemoryInstanceRepository()
        service = InstanceService(repo, authorization=AllowAllAuthorizationAdapter())
        admin = _principal(admin=True)
        legacy = _instance("legacy", visibility=InstanceVisibility.SYSTEM, tenant_id=None)
        legacy = replace(legacy, base_url="http://legacy.example.com", config={}, enabled=False)
        repo.instances[legacy.id] = legacy

        updated = await service.update_instance(
            admin,
            legacy.id,
            enabled=True,
            base_url="https://legacy.example.com",
        )

        assert updated.enabled is True
        assert updated.base_url == "https://legacy.example.com"
