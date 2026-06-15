"""Tests for Guild-backed Volundr target discovery."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.domain.models import InstanceKind, InstanceVisibility, Principal, RegisteredInstance
from tests.test_ting.conftest import StubCredentialStore
from ting.adapters.volundr_factory import LocalVolundrAdapterFactory, VolundrAdapterFactory
from ting.adapters.volundr_http import VolundrHTTPAdapter

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
async def test_registry_error_returns_empty() -> None:
    factory = VolundrAdapterFactory(
        FailingGuildRegistry(),
        StubCredentialStore(),
        allow_unauthenticated=True,
    )

    assert await factory.for_owner("owner-1") == []


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
