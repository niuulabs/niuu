"""Local authority observes revocation without resynchronizing stale IDP claims."""

from unittest.mock import AsyncMock

import pytest

from identity.adapters.identity import EnvoyHeaderIdentityAdapter
from identity.models import TenantMembership, TenantRole, User, UserStatus
from niuu.ports.identity import InvalidTokenError


async def test_local_membership_removal_and_downgrade_override_idp():
    repo = AsyncMock()
    repo.get.return_value = User(id="alice", email="a@test", status=UserStatus.ACTIVE)
    service = AsyncMock()
    adapter = EnvoyHeaderIdentityAdapter(
        user_repository=repo, tenant_service=service, membership_authority="local"
    )
    headers = {"x-auth-user-id": "alice", "x-auth-tenant": "acme", "x-auth-roles": "volundr:admin"}
    repo.get_memberships.return_value = [TenantMembership("alice", "acme", TenantRole.VIEWER)]
    principal = await adapter.validate_headers(headers)
    assert principal.roles == ["volundr:viewer"]
    await adapter.get_or_provision_user(principal)
    service.add_member.assert_not_called()
    repo.get_memberships.return_value = []
    with pytest.raises(InvalidTokenError, match="membership"):
        await adapter.validate_headers(headers)
    service.add_member.assert_not_called()


async def test_local_authority_does_not_auto_enroll_unknown_users():
    repo = AsyncMock()
    repo.get.return_value = None
    adapter = EnvoyHeaderIdentityAdapter(user_repository=repo, membership_authority="local")
    with pytest.raises(InvalidTokenError):
        await adapter.validate_headers({"x-auth-user-id": "unknown", "x-auth-tenant": "acme"})
    repo.create.assert_not_called()


def test_unknown_authority_is_configuration_error():
    with pytest.raises(ValueError, match="membership_authority"):
        EnvoyHeaderIdentityAdapter(user_repository=AsyncMock(), membership_authority="fallback")
