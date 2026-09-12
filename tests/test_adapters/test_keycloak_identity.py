"""Tests for EnvoyHeaderIdentityAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.identity import EnvoyHeaderIdentityAdapter
from volundr.domain.models import Principal, User, UserStatus
from volundr.domain.ports import InvalidTokenError, UserProvisioningError


def _make_adapter(user_repo=None):
    if user_repo is None:
        user_repo = AsyncMock()
    return EnvoyHeaderIdentityAdapter(
        user_repository=user_repo,
        user_id_header="x-auth-user-id",
        email_header="x-auth-email",
        tenant_header="x-auth-tenant",
        roles_header="x-auth-roles",
        default_tenant_id="default",
    )


class TestValidateToken:
    """validate_token should reject in envoy mode."""

    async def test_empty_token_raises(self):
        adapter = _make_adapter()
        with pytest.raises(InvalidTokenError, match="Empty"):
            await adapter.validate_token("")

    async def test_non_empty_token_raises(self):
        adapter = _make_adapter()
        with pytest.raises(InvalidTokenError, match="requires headers"):
            await adapter.validate_token("Bearer some-token")


class TestValidateHeaders:
    """Tests for header-based principal extraction."""

    async def test_valid_headers_returns_principal(self):
        adapter = _make_adapter()
        headers = {
            "x-auth-user-id": "user-123",
            "x-auth-email": "user@example.com",
            "x-auth-tenant": "acme",
            "x-auth-roles": "volundr:admin,volundr:developer",
        }

        principal = await adapter.validate_headers(headers)
        assert principal.user_id == "user-123"
        assert principal.email == "user@example.com"
        assert principal.tenant_id == "acme"
        assert "volundr:admin" in principal.roles
        assert "volundr:developer" in principal.roles

    async def test_missing_user_id_raises(self):
        adapter = _make_adapter()
        headers = {
            "x-auth-email": "user@example.com",
        }

        with pytest.raises(InvalidTokenError, match="x-auth-user-id"):
            await adapter.validate_headers(headers)

    async def test_default_tenant_when_missing(self):
        adapter = _make_adapter()
        headers = {
            "x-auth-user-id": "u1",
            "x-auth-email": "a@b.com",
        }

        principal = await adapter.validate_headers(headers)
        assert principal.tenant_id == "default"

    async def test_no_roles_does_not_grant_developer(self):
        adapter = _make_adapter()
        headers = {
            "x-auth-user-id": "u1",
            "x-auth-email": "a@b.com",
        }

        principal = await adapter.validate_headers(headers)
        assert principal.roles == []

    async def test_empty_roles_header_grants_no_roles(self):
        adapter = _make_adapter()
        headers = {
            "x-auth-user-id": "u1",
            "x-auth-email": "a@b.com",
            "x-auth-roles": "",
        }

        principal = await adapter.validate_headers(headers)
        assert principal.roles == []

    async def test_single_role(self):
        adapter = _make_adapter()
        headers = {
            "x-auth-user-id": "u1",
            "x-auth-email": "a@b.com",
            "x-auth-roles": "volundr:viewer",
        }

        principal = await adapter.validate_headers(headers)
        assert principal.roles == ["volundr:viewer"]

    async def test_custom_header_names(self):
        adapter = EnvoyHeaderIdentityAdapter(
            user_repository=AsyncMock(),
            user_id_header="x-custom-sub",
            email_header="x-custom-email",
            tenant_header="x-custom-org",
            roles_header="x-custom-perms",
        )
        headers = {
            "x-custom-sub": "u99",
            "x-custom-email": "custom@test.com",
            "x-custom-org": "custom-org",
            "x-custom-perms": "volundr:admin",
        }

        principal = await adapter.validate_headers(headers)
        assert principal.user_id == "u99"
        assert principal.tenant_id == "custom-org"


class TestGetOrProvisionUser:
    """Tests for JIT user provisioning."""

    async def test_existing_active_user_returned(self):
        user_repo = AsyncMock()
        existing = User(id="u1", email="a@b.com", status=UserStatus.ACTIVE)
        user_repo.get.return_value = existing

        adapter = _make_adapter(user_repo)
        principal = Principal(user_id="u1", email="a@b.com", tenant_id="t1", roles=[])

        user = await adapter.get_or_provision_user(principal)
        assert user.id == "u1"
        user_repo.create.assert_not_called()

    async def test_provisioning_user_raises(self):
        user_repo = AsyncMock()
        provisioning = User(id="u1", email="a@b.com", status=UserStatus.PROVISIONING)
        user_repo.get.return_value = provisioning

        adapter = _make_adapter(user_repo)
        principal = Principal(user_id="u1", email="a@b.com", tenant_id="t1", roles=[])

        with pytest.raises(UserProvisioningError):
            await adapter.get_or_provision_user(principal)

    async def test_new_user_provisioned(self):
        user_repo = AsyncMock()
        user_repo.get.return_value = None
        created = User(id="u1", email="a@b.com", status=UserStatus.PROVISIONING)
        updated = User(id="u1", email="a@b.com", status=UserStatus.ACTIVE)
        user_repo.create.return_value = created
        user_repo.update.return_value = updated

        adapter = _make_adapter(user_repo)
        principal = Principal(user_id="u1", email="a@b.com", tenant_id="t1", roles=[])

        user = await adapter.get_or_provision_user(principal)
        assert user.status == UserStatus.ACTIVE
        user_repo.create.assert_called_once()
        user_repo.update.assert_called_once()

    async def test_provisioning_failure_marks_failed(self):
        user_repo = AsyncMock()
        user_repo.get.return_value = None
        created = User(id="u1", email="a@b.com", status=UserStatus.PROVISIONING)
        user_repo.create.return_value = created
        # First update (to ACTIVE) raises, second update (to FAILED) succeeds
        failed = User(id="u1", email="a@b.com", status=UserStatus.FAILED)
        user_repo.update.side_effect = [Exception("boom"), failed]

        adapter = _make_adapter(user_repo)
        principal = Principal(user_id="u1", email="a@b.com", tenant_id="t1", roles=[])

        with pytest.raises(UserProvisioningError):
            await adapter.get_or_provision_user(principal)

        assert user_repo.update.call_count == 2


@pytest.mark.parametrize("status", [UserStatus.SUSPENDED, UserStatus.FAILED])
async def test_disabled_user_is_denied_before_authority_or_membership_sync(status):
    user_repo = AsyncMock()
    user_repo.get.return_value = User(id="alice", email="alice@test", status=status)
    tenant_service = AsyncMock()
    adapter = EnvoyHeaderIdentityAdapter(user_repository=user_repo, tenant_service=tenant_service)
    with pytest.raises(InvalidTokenError, match="not active"):
        await adapter.validate_headers({"x-auth-user-id": "alice", "x-auth-tenant": "acme"})
    with pytest.raises(InvalidTokenError, match="not active"):
        await adapter.get_or_provision_user(
            Principal("alice", "alice@test", "acme", ["volundr:admin"])
        )
    tenant_service.add_member.assert_not_called()
    user_repo.update.assert_not_called()
