"""Credential namespace isolation and policy denial before secret-store mutation."""

from unittest.mock import AsyncMock

import pytest

from credentials.mount_strategies import SecretMountStrategyRegistry
from credentials.service import CredentialService
from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.ports import AuthorizationDeniedError
from niuu.domain.models import Principal, SecretType
from volundr.adapters.outbound.memory_credential_store import MemoryCredentialStore


def principal(user="alice", tenant="acme", role="developer"):
    return Principal(user, "", tenant, [f"volundr:{role}"])


async def test_private_credentials_remain_private_from_other_users_and_admins():
    store = MemoryCredentialStore()
    service = CredentialService(
        store, SecretMountStrategyRegistry(), authorization=CedarAuthorizationAdapter()
    )
    await service.create(
        principal(), "user", "alice", "key", SecretType.GENERIC, {"token": "private"}
    )
    for actor in [principal("bob"), principal("bob", role="admin")]:
        assert await service.list(actor, "user", "alice") == []
        for method in [service.get, service.delete]:
            with pytest.raises(AuthorizationDeniedError):
                await method(actor, "user", "alice", "key")
    assert await service.get(principal(), "user", "alice", "key") is not None


async def test_shared_credentials_are_tenant_bound_and_admin_managed():
    store = MemoryCredentialStore()
    service = CredentialService(
        store, SecretMountStrategyRegistry(), authorization=CedarAuthorizationAdapter()
    )
    admin = principal(role="admin")
    await service.create(
        admin, "tenant", "acme", "shared", SecretType.GENERIC, {"token": "private"}
    )
    assert len(await service.list(admin, "tenant", "acme")) == 1
    for actor in [principal(), principal(tenant="other", role="admin")]:
        assert await service.list(actor, "tenant", "acme") == []
        with pytest.raises(AuthorizationDeniedError):
            await service.delete(actor, "tenant", "acme", "shared")


async def test_upsert_requires_update_authority_before_touching_store():
    authorization = AsyncMock()
    authorization.is_allowed.side_effect = [True, False]
    store = AsyncMock()
    service = CredentialService(store, SecretMountStrategyRegistry(), authorization=authorization)
    with pytest.raises(AuthorizationDeniedError):
        await service.create(
            principal(), "user", "alice", "key", SecretType.GENERIC, {"token": "new"}
        )
    store.store.assert_not_called()
    assert [c.args[1] for c in authorization.is_allowed.await_args_list] == ["create", "update"]


@pytest.mark.parametrize(
    "owner_type,owner,tenant",
    [("unknown", "alice", "acme"), ("user", "", "acme"), ("user", "alice", "")],
)
async def test_missing_or_unknown_ownership_denies_before_store_access(owner_type, owner, tenant):
    store = AsyncMock()
    service = CredentialService(
        store, SecretMountStrategyRegistry(), authorization=CedarAuthorizationAdapter()
    )
    with pytest.raises(AuthorizationDeniedError):
        await service.get(principal(tenant=tenant), owner_type, owner, "key")
    store.get.assert_not_called()
