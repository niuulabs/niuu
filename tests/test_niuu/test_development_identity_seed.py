"""Startup provisions development identities only for the explicit dev adapter."""

from unittest.mock import AsyncMock

import pytest

from identity.adapters.identity import AllowAllIdentityAdapter
from identity.models import TenantMembership, TenantRole, User
from niuu.service_runtime import seed_development_identity


@pytest.mark.asyncio
async def test_seed_creates_the_user_and_repairs_existing_default_membership():
    users = AsyncMock()
    user = User(id="dev-user", email="dev@localhost")
    users.get.side_effect = [None, user]
    identity = AllowAllIdentityAdapter(user_repository=users)
    await seed_development_identity(identity, users)
    await seed_development_identity(identity, users)
    users.create.assert_awaited_once()
    assert users.add_membership.await_count == 2
    users.add_membership.assert_awaited_with(
        TenantMembership(user_id="dev-user", tenant_id="default", role=TenantRole.ADMIN)
    )


@pytest.mark.asyncio
async def test_authenticated_adapters_never_seed_a_development_admin():
    users = AsyncMock()
    await seed_development_identity(object(), users)
    users.create.assert_not_called()
    users.add_membership.assert_not_called()
