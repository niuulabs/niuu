"""Tests for VolundrAdapterFactory — multi-cluster support."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    IntegrationConnection,
    IntegrationType,
    Principal,
    RegisteredInstance,
)
from niuu.ports.instances import InstanceRepository
from tests.test_ting.conftest import StubCredentialStore, StubIntegrationRepo
from ting.adapters.volundr_factory import (
    DEFAULT_VOLUNDR_URL,
    LocalVolundrAdapterFactory,
    VolundrAdapterFactory,
)
from ting.adapters.volundr_http import VolundrHTTPAdapter

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_connection(
    *,
    conn_id: str = "conn-1",
    enabled: bool = True,
    integration_type: IntegrationType = IntegrationType.CODE_FORGE,
    credential_name: str = "volundr-pat",
    config: dict | None = None,
    slug: str = "",
) -> IntegrationConnection:
    return IntegrationConnection(
        id=conn_id,
        owner_id="owner-1",
        integration_type=integration_type,
        adapter="ting.adapters.volundr_http.VolundrHTTPAdapter",
        credential_name=credential_name,
        config={"url": "http://volundr-test:8000"} if config is None else config,
        enabled=enabled,
        created_at=_NOW,
        updated_at=_NOW,
        slug=slug,
    )


def _make_instance(
    *,
    instance_id: str,
    name: str,
    base_url: str,
    visibility: InstanceVisibility,
    owner_id: str | None = None,
    tenant_id: str | None = None,
    is_default: bool = False,
    config: dict | None = None,
) -> RegisteredInstance:
    return RegisteredInstance(
        id=instance_id,
        kind=InstanceKind.VOLUNDR,
        slug=name.lower().replace(" ", "-"),
        name=name,
        base_url=base_url,
        visibility=visibility,
        owner_id=owner_id,
        tenant_id=tenant_id,
        enabled=True,
        is_default=is_default,
        config={} if config is None else config,
        created_at=_NOW,
        updated_at=_NOW,
    )


class StubInstanceRepo(InstanceRepository):
    def __init__(self, instances: list[RegisteredInstance]) -> None:
        self._instances = list(instances)

    async def list_instances(self, kind: InstanceKind | None = None) -> list[RegisteredInstance]:
        if kind is None:
            return list(self._instances)
        return [instance for instance in self._instances if instance.kind == kind]

    async def get_instance(self, instance_id: str) -> RegisteredInstance | None:
        for instance in self._instances:
            if instance.id == instance_id:
                return instance
        return None

    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        self._instances = [item for item in self._instances if item.id != instance.id]
        self._instances.append(instance)
        return instance

    async def delete_instance(self, instance_id: str) -> None:
        self._instances = [item for item in self._instances if item.id != instance_id]


class FailingInstanceRepo(InstanceRepository):
    async def list_instances(
        self,
        kind: InstanceKind | None = None,
    ) -> list[RegisteredInstance]:
        raise RuntimeError("db unavailable")

    async def get_instance(self, instance_id: str) -> RegisteredInstance | None:
        return None

    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        return instance

    async def delete_instance(self, instance_id: str) -> None:
        return None


# ---------------------------------------------------------------------------
# Tests — for_owner (returns only authenticated adapters, no fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_code_forge_connection_returns_empty() -> None:
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_disabled_connection_returns_empty() -> None:
    conn = _make_connection(enabled=False)
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-123"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_enabled_connection_valid_cred_returns_adapter() -> None:
    conn = _make_connection(enabled=True)
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-123"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert isinstance(result[0], VolundrHTTPAdapter)
    assert result[0]._api_key == "tok-123"
    assert result[0]._base_url == "http://volundr-test:8000"


@pytest.mark.asyncio
async def test_credential_store_returns_none_returns_empty() -> None:
    conn = _make_connection(enabled=True)
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(values={}),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_credential_store_returns_none_allows_unauthenticated_in_dev() -> None:
    conn = _make_connection(enabled=True, config={"url": "http://cluster-a:8000", "name": "alpha"})
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(values={}),
        allow_unauthenticated=True,
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert result[0]._base_url == "http://cluster-a:8000"
    assert result[0]._api_key is None
    assert result[0]._name == "alpha"


@pytest.mark.asyncio
async def test_uses_default_url_when_not_in_config() -> None:
    conn = _make_connection(enabled=True, config={})
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-456"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert result[0]._base_url == DEFAULT_VOLUNDR_URL


@pytest.mark.asyncio
async def test_multiple_connections_return_multiple_adapters() -> None:
    conn1 = _make_connection(
        conn_id="conn-1",
        config={"url": "http://cluster-a:8000", "name": "alpha"},
        credential_name="pat-a",
    )
    conn2 = _make_connection(
        conn_id="conn-2",
        config={"url": "http://cluster-b:8000", "name": "beta"},
        credential_name="pat-b",
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn1, conn2]),
        credential_store=StubCredentialStore(
            values={
                "user:owner-1:pat-a": {"token": "tok-a"},
                "user:owner-1:pat-b": {"token": "tok-b"},
            }
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 2
    assert result[0]._base_url == "http://cluster-a:8000"
    assert result[0]._api_key == "tok-a"
    assert result[0]._name == "alpha"
    assert result[1]._base_url == "http://cluster-b:8000"
    assert result[1]._api_key == "tok-b"
    assert result[1]._name == "beta"


@pytest.mark.asyncio
async def test_skips_connection_with_no_cred() -> None:
    """One connection has a credential, the other doesn't — only the valid one is returned."""
    conn1 = _make_connection(
        conn_id="conn-1",
        config={"url": "http://cluster-a:8000"},
        credential_name="pat-a",
    )
    conn2 = _make_connection(
        conn_id="conn-2",
        config={"url": "http://cluster-b:8000"},
        credential_name="pat-b",
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn1, conn2]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:pat-a": {"token": "tok-a"}},
        ),
    )
    result = await factory.for_owner("owner-1")
    assert len(result) == 1
    assert result[0]._base_url == "http://cluster-a:8000"


@pytest.mark.asyncio
async def test_adapter_name_uses_slug_when_no_config_name() -> None:
    conn = _make_connection(
        config={"url": "http://volundr:8000"},
        slug="my-volundr",
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-1"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert result[0]._name == "my-volundr"


@pytest.mark.asyncio
async def test_adapter_name_falls_back_to_conn_id() -> None:
    conn = _make_connection(config={"url": "http://volundr:8000"})
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:volundr-pat": {"token": "tok-1"}}
        ),
    )
    result = await factory.for_owner("owner-1")
    assert result[0]._name == "conn-1"


# ---------------------------------------------------------------------------
# Tests — primary_for_owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_primary_for_owner_returns_first() -> None:
    conn1 = _make_connection(
        conn_id="conn-1",
        config={"url": "http://primary:8000"},
        credential_name="pat-a",
    )
    conn2 = _make_connection(
        conn_id="conn-2",
        config={"url": "http://secondary:8000"},
        credential_name="pat-b",
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn1, conn2]),
        credential_store=StubCredentialStore(
            values={
                "user:owner-1:pat-a": {"token": "tok-a"},
                "user:owner-1:pat-b": {"token": "tok-b"},
            }
        ),
    )
    result = await factory.primary_for_owner("owner-1")
    assert result is not None
    assert isinstance(result, VolundrHTTPAdapter)
    assert result._base_url == "http://primary:8000"


@pytest.mark.asyncio
async def test_primary_for_owner_returns_none_when_empty() -> None:
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
    )
    result = await factory.primary_for_owner("owner-1")
    assert result is None


# ---------------------------------------------------------------------------
# Tests — error handling in _resolve_connections
# ---------------------------------------------------------------------------


class _FailingCredentialStore(StubCredentialStore):
    """Credential store that raises on get_value."""

    async def get_value(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        raise RuntimeError("credential store unavailable")


@pytest.mark.asyncio
async def test_credential_store_error_skips_connection() -> None:
    """If the credential store raises, the connection should be skipped (not crash)."""
    conn = _make_connection(enabled=True)
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[conn]),
        credential_store=_FailingCredentialStore(),
    )
    result = await factory.for_owner("owner-1")
    assert result == []


@pytest.mark.asyncio
async def test_for_principal_includes_tenant_and_user_scoped_instances() -> None:
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
        instance_repo=StubInstanceRepo(
            instances=[
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    visibility=InstanceVisibility.SYSTEM,
                    is_default=True,
                ),
                _make_instance(
                    instance_id="tenant-1",
                    name="Tenant Beta",
                    base_url="http://beta:8000",
                    visibility=InstanceVisibility.TENANT,
                    tenant_id="tenant-a",
                ),
                _make_instance(
                    instance_id="user-1",
                    name="User Gamma",
                    base_url="http://gamma:8000",
                    visibility=InstanceVisibility.USER,
                    owner_id="owner-1",
                ),
                _make_instance(
                    instance_id="tenant-other",
                    name="Tenant Other",
                    base_url="http://other:8000",
                    visibility=InstanceVisibility.TENANT,
                    tenant_id="tenant-b",
                ),
            ]
        ),
        allow_unauthenticated=True,
    )
    principal = Principal(
        user_id="owner-1",
        email="owner-1@example.com",
        tenant_id="tenant-a",
        roles=["volundr:developer"],
    )

    result = await factory.for_principal(principal)

    assert [adapter.target_id for adapter in result] == ["system-1", "tenant-1", "user-1"]
    assert [adapter.name for adapter in result] == ["System Alpha", "Tenant Beta", "User Gamma"]


@pytest.mark.asyncio
async def test_primary_for_principal_prefers_default_visible_instance() -> None:
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
        instance_repo=StubInstanceRepo(
            instances=[
                _make_instance(
                    instance_id="tenant-1",
                    name="Tenant Beta",
                    base_url="http://beta:8000",
                    visibility=InstanceVisibility.TENANT,
                    tenant_id="tenant-a",
                ),
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    visibility=InstanceVisibility.SYSTEM,
                    is_default=True,
                ),
            ]
        ),
        allow_unauthenticated=True,
    )
    principal = Principal(
        user_id="owner-1",
        email="owner-1@example.com",
        tenant_id="tenant-a",
        roles=["volundr:developer"],
    )

    result = await factory.primary_for_principal(principal)

    assert result is not None
    assert result.target_id == "system-1"


@pytest.mark.asyncio
async def test_registered_instance_uses_configured_credential_name_for_api_key() -> None:
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(
            values={"user:owner-1:shared-volundr-pat": {"token": "tok-instance"}}
        ),
        instance_repo=StubInstanceRepo(
            instances=[
                _make_instance(
                    instance_id="system-1",
                    name="System Alpha",
                    base_url="http://alpha:8000",
                    visibility=InstanceVisibility.SYSTEM,
                    config={"credential_name": "shared-volundr-pat"},
                )
            ]
        ),
    )

    result = await factory.for_owner("owner-1")

    assert len(result) == 1
    assert result[0]._api_key == "tok-instance"


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

    assert len(owner_adapters) == 1
    assert owner_adapters[0] is owner_primary
    assert principal_adapters[0] is owner_primary
    assert principal_primary is owner_primary


@pytest.mark.asyncio
async def test_for_principal_skips_unavailable_or_invisible_registered_instances() -> None:
    principal = Principal(
        user_id="owner-1",
        email="owner-1@example.com",
        tenant_id="tenant-a",
        roles=[],
    )
    factory = VolundrAdapterFactory(
        integration_repo=StubIntegrationRepo(connections=[]),
        credential_store=StubCredentialStore(),
        instance_repo=StubInstanceRepo(
            instances=[
                _make_instance(
                    instance_id="disabled",
                    name="Disabled",
                    base_url="http://disabled:8000",
                    visibility=InstanceVisibility.SYSTEM,
                ),
                RegisteredInstance(
                    id="custom",
                    kind=InstanceKind.VOLUNDR,
                    slug="custom",
                    name="Custom",
                    base_url="http://custom:8000",
                    visibility="custom",  # type: ignore[arg-type]
                    owner_id=None,
                    tenant_id=None,
                    enabled=True,
                    is_default=False,
                    config={},
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
                _make_instance(
                    instance_id="tenant-other",
                    name="Tenant Other",
                    base_url="http://other:8000",
                    visibility=InstanceVisibility.TENANT,
                    tenant_id="tenant-b",
                ),
            ]
        ),
        allow_unauthenticated=True,
    )
    factory._instance_repo = FailingInstanceRepo()
    assert await factory.for_principal(principal) == []

    factory._instance_repo = StubInstanceRepo(
        instances=[
            RegisteredInstance(
                id="custom",
                kind=InstanceKind.VOLUNDR,
                slug="custom",
                name="Custom",
                base_url="http://custom:8000",
                visibility="custom",  # type: ignore[arg-type]
                owner_id=None,
                tenant_id=None,
                enabled=True,
                is_default=False,
                config={},
                created_at=_NOW,
                updated_at=_NOW,
            ),
            _make_instance(
                instance_id="disabled",
                name="Disabled",
                base_url="http://disabled:8000",
                visibility=InstanceVisibility.SYSTEM,
            ),
            _make_instance(
                instance_id="tenant-visible",
                name="Tenant Visible",
                base_url="http://tenant:8000",
                visibility=InstanceVisibility.TENANT,
                tenant_id="tenant-a",
            ),
        ]
    )
    factory._instance_repo._instances[1] = RegisteredInstance(
        **{**factory._instance_repo._instances[1].__dict__, "enabled": False}
    )

    result = await factory.for_principal(principal)

    assert [adapter.target_id for adapter in result] == ["tenant-visible"]
