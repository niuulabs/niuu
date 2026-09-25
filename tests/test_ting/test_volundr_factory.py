"""Tests for Guild-backed Volundr target discovery."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from niuu.domain.models import InstanceKind, InstanceVisibility, Principal, RegisteredInstance
from tests.test_ting.conftest import StubCredentialStore
from ting.adapters.volundr_factory import (
    CredentialBindingError,
    GuildRegistryUnavailableError,
    LocalVolundrAdapterFactory,
    VolundrAdapterFactory,
)
from ting.adapters.volundr_http import VolundrHTTPAdapter


class FailingCredentialStore:
    """Simulates a credential-store outage on every lookup."""

    async def get_value(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        raise RuntimeError("credential store unreachable")


_NOW = datetime.now(tz=UTC)


def _make_instance(
    *,
    instance_id: str,
    name: str,
    base_url: str,
    is_default: bool = False,
    config: dict | None = None,
    tags: list[str] | None = None,
) -> RegisteredInstance:
    return RegisteredInstance(
        id=instance_id,
        kind=InstanceKind.VOLUNDR,
        slug=name.lower().replace(" ", "-"),
        name=name,
        base_url=base_url,
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id=None,
        enabled=True,
        is_default=is_default,
        config={} if config is None else config,
        created_at=_NOW,
        updated_at=_NOW,
        tags=[] if tags is None else tags,
    )


class StubGuildRegistry:
    def __init__(self, instances: list[RegisteredInstance]) -> None:
        self.instances = list(instances)
        self.principals: list[Principal] = []

    async def list_volundr_targets(self, principal: Principal) -> list[RegisteredInstance]:
        self.principals.append(principal)
        return list(self.instances)


class FailingGuildRegistry:
    async def list_volundr_targets(self, principal: Principal) -> list[RegisteredInstance]:
        raise RuntimeError("guild unavailable")


class StaticAuth:
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer service-token"}


@pytest.mark.asyncio
async def test_for_principal_discovers_targets_from_guild_registry() -> None:
    registry = StubGuildRegistry(
        [
            _make_instance(
                instance_id="tenant-1",
                name="Tenant Beta",
                base_url="http://beta:8000",
                tags=["tenant-a"],
            ),
            _make_instance(
                instance_id="system-1",
                name="System Alpha",
                base_url="http://alpha:8000",
                is_default=True,
                tags=["system"],
            ),
        ]
    )
    principal = Principal(
        user_id="owner-1",
        email="owner-1@example.com",
        tenant_id="tenant-a",
        roles=["volundr:developer"],
    )
    factory = VolundrAdapterFactory(
        registry,
        StubCredentialStore(),
        allow_unauthenticated=True,
    )

    result = await factory.for_principal(principal)

    assert registry.principals == [principal]
    assert [adapter.target_id for adapter in result] == ["system-1", "tenant-1"]
    assert [adapter.name for adapter in result] == ["System Alpha", "Tenant Beta"]
    assert [adapter.tags for adapter in result] == [["system"], ["tenant-a"]]


@pytest.mark.asyncio
async def test_primary_for_principal_prefers_default_guild_target() -> None:
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="tenant-1",
                    name="Tenant Beta",
                    base_url="http://beta:8000",
                ),
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    is_default=True,
                ),
            ]
        ),
        StubCredentialStore(),
        allow_unauthenticated=True,
    )

    result = await factory.primary_for_principal(
        Principal(
            user_id="owner-1",
            email="owner-1@example.com",
            tenant_id="tenant-a",
            roles=["volundr:developer"],
        )
    )

    assert result is not None
    assert result.target_id == "system-1"


@pytest.mark.asyncio
async def test_registered_instance_uses_configured_credential_name_for_api_key() -> None:
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    config={"credential_name": "shared-volundr-pat"},
                )
            ]
        ),
        StubCredentialStore(values={"user:owner-1:shared-volundr-pat": {"token": "tok-instance"}}),
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 1
    assert result[0]._api_key == "tok-instance"


@pytest.mark.asyncio
async def test_credential_binding_is_the_canonical_shape_the_guild_ui_writes() -> None:
    """The register dialog writes config.credentialBinding = {name, scope} —
    Ting must read what the operator actually bound instead of a separate,
    never-populated config.credential_name key."""
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    config={"credentialBinding": {"name": "bound-pat", "scope": "user"}},
                )
            ]
        ),
        StubCredentialStore(values={"user:owner-1:bound-pat": {"token": "tok-bound"}}),
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 1
    assert result[0]._api_key == "tok-bound"


@pytest.mark.asyncio
async def test_tenant_scoped_credential_binding_resolves_against_the_instance_tenant() -> None:
    """A tenant-scoped binding must not be looked up under the requesting
    user's id — that always missed before, silently forcing every binding
    to behave as user-scoped regardless of what the operator selected."""
    instance = replace(
        _make_instance(
            instance_id="system-1",
            name="System Alpha",
            base_url="http://alpha:8000",
            config={"credentialBinding": {"name": "shared-pat", "scope": "tenant"}},
        ),
        tenant_id="tenant-a",
    )
    factory = VolundrAdapterFactory(
        StubGuildRegistry([instance]),
        StubCredentialStore(values={"tenant:tenant-a:shared-pat": {"token": "tok-tenant"}}),
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 1
    assert result[0]._api_key == "tok-tenant"


@pytest.mark.asyncio
async def test_tenant_scoped_binding_without_an_instance_tenant_raises() -> None:
    """Silently falling back to the requesting user's id would look up the
    wrong credential store entry (or none at all) with no signal that the
    operator's tenant-scoped binding cannot actually be resolved."""
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    config={"credentialBinding": {"name": "shared-pat", "scope": "tenant"}},
                )
            ]
        ),
        StubCredentialStore(),
    )

    with pytest.raises(CredentialBindingError, match="tenant_id"):
        await factory.for_owner("owner-1")


@pytest.mark.asyncio
async def test_unknown_credential_scope_raises_instead_of_reaching_the_store() -> None:
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    config={"credentialBinding": {"name": "shared-pat", "scope": "planet"}},
                )
            ]
        ),
        StubCredentialStore(),
    )

    with pytest.raises(CredentialBindingError, match="planet"):
        await factory.for_owner("owner-1")


@pytest.mark.asyncio
async def test_credential_binding_with_an_empty_name_raises() -> None:
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    config={"credentialBinding": {"name": "  ", "scope": "user"}},
                )
            ]
        ),
        StubCredentialStore(),
    )

    with pytest.raises(CredentialBindingError, match="no name"):
        await factory.for_owner("owner-1")


@pytest.mark.asyncio
async def test_a_malformed_credential_binding_raises_instead_of_falling_back_to_legacy() -> None:
    """Once credentialBinding is present it is authoritative — a malformed
    value must not silently defer to a stale legacy credential_name."""
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    config={
                        "credentialBinding": "not-an-object",
                        "credential_name": "stale-legacy-name",
                    },
                )
            ]
        ),
        StubCredentialStore(values={"user:owner-1:stale-legacy-name": {"token": "tok"}}),
    )

    with pytest.raises(CredentialBindingError, match="must be an object"):
        await factory.for_owner("owner-1")


@pytest.mark.asyncio
async def test_a_credential_store_outage_raises_instead_of_dropping_every_instance() -> None:
    """The old code caught this around the whole per-instance body, so a
    credential-store outage silently dropped every configured instance and
    looked exactly like "no connections configured"."""
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    config={"credentialBinding": {"name": "shared-pat", "scope": "user"}},
                )
            ]
        ),
        FailingCredentialStore(),
    )

    with pytest.raises(RuntimeError, match="credential store unreachable"):
        await factory.for_owner("owner-1")


@pytest.mark.asyncio
async def test_target_without_configured_credential_is_allowed_for_request_time_auth() -> None:
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                )
            ]
        ),
        StubCredentialStore(),
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 1
    assert result[0]._api_key is None


@pytest.mark.asyncio
async def test_passes_target_service_auth_to_resolved_adapters() -> None:
    target_auth = StaticAuth()
    factory = VolundrAdapterFactory(
        StubGuildRegistry(
            [
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                )
            ]
        ),
        StubCredentialStore(),
        target_auth=target_auth,
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 1
    assert result[0]._auth is target_auth


@pytest.mark.asyncio
async def test_skips_credentialed_target_without_token_unless_dev_allows_it() -> None:
    registry = StubGuildRegistry(
        [
            _make_instance(
                instance_id="system-1",
                name="System Alpha",
                base_url="http://alpha:8000",
                config={"credential_name": "missing"},
            )
        ]
    )

    strict = VolundrAdapterFactory(registry, StubCredentialStore())
    permissive = VolundrAdapterFactory(
        registry,
        StubCredentialStore(),
        allow_unauthenticated=True,
    )

    assert await strict.for_owner("owner-1") == []
    allowed = await permissive.for_owner("owner-1")
    assert len(allowed) == 1
    assert allowed[0]._api_key is None


@pytest.mark.asyncio
async def test_registry_error_raises_instead_of_looking_like_no_connections() -> None:
    """A Guild outage must not be indistinguishable from "this user has no
    Volundr connections configured" — dispatch and the activity subscriber
    need to tell those two apart (see .claude/rules/no-fallbacks.md)."""
    factory = VolundrAdapterFactory(
        FailingGuildRegistry(),
        StubCredentialStore(),
        allow_unauthenticated=True,
    )

    with pytest.raises(GuildRegistryUnavailableError, match="owner-1"):
        await factory.for_owner("owner-1")


@pytest.mark.asyncio
async def test_local_factory_reuses_single_adapter_for_all_entrypoints() -> None:
    factory = LocalVolundrAdapterFactory("http://local:8000")
    principal = Principal(
        user_id="owner-1",
        email="owner-1@example.com",
        tenant_id="tenant-a",
        roles=[],
    )

    owner_adapters = await factory.for_owner("owner-1")
    owner_primary = await factory.primary_for_owner("owner-1")
    principal_adapters = await factory.for_principal(principal)
    principal_primary = await factory.primary_for_principal(principal)

    assert isinstance(owner_adapters[0], VolundrHTTPAdapter)
    assert owner_adapters[0] is owner_primary
    assert principal_adapters[0] is owner_primary
    assert principal_primary is owner_primary
