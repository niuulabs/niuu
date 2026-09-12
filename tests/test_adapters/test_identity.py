"""Tests for identity adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.identity import AllowAllIdentityAdapter, EnvoyHeaderIdentityAdapter
from volundr.domain.models import Principal, User, UserStatus
from volundr.domain.ports import InvalidTokenError


class TestAllowAllIdentityAdapter:
    """Tests for AllowAllIdentityAdapter."""

    def _make_adapter(self, user_repo=None):
        if user_repo is None:
            user_repo = AsyncMock()
        return AllowAllIdentityAdapter(user_repository=user_repo, default_tenant_id="test-tenant")

    async def test_validate_token_returns_principal(self):
        adapter = self._make_adapter()
        principal = await adapter.validate_token("any-token")

        assert isinstance(principal, Principal)
        assert principal.user_id == "dev-user"
        assert principal.email == "dev@localhost"
        assert principal.tenant_id == "test-tenant"
        assert "volundr:admin" in principal.roles

    async def test_validate_token_empty_raises(self):
        adapter = self._make_adapter()
        with pytest.raises(InvalidTokenError):
            await adapter.validate_token("")

    async def test_get_or_provision_existing_user(self):
        user_repo = AsyncMock()
        existing = User(id="dev-user", email="dev@localhost", status=UserStatus.ACTIVE)
        user_repo.get.return_value = existing

        adapter = self._make_adapter(user_repo)
        principal = Principal(
            user_id="dev-user",
            email="dev@localhost",
            tenant_id="default",
            roles=["volundr:admin"],
        )

        user = await adapter.get_or_provision_user(principal)
        assert user.id == "dev-user"
        user_repo.create.assert_not_called()

    async def test_get_or_provision_new_user(self):
        user_repo = AsyncMock()
        user_repo.get.return_value = None
        created = User(id="dev-user", email="dev@localhost", status=UserStatus.ACTIVE)
        user_repo.create.return_value = created

        adapter = self._make_adapter(user_repo)
        principal = Principal(
            user_id="dev-user",
            email="dev@localhost",
            tenant_id="default",
            roles=["volundr:admin"],
        )

        user = await adapter.get_or_provision_user(principal)
        assert user.id == "dev-user"
        user_repo.create.assert_called_once()


class TestEnvoyHeaderIdentityAdapter:
    """Tests for EnvoyHeaderIdentityAdapter."""

    async def test_missing_roles_cannot_provision_developer_membership(self):
        from niuu.domain.models import Principal
        from niuu.ports.identity import InvalidTokenError

        tenants = AsyncMock()
        adapter = EnvoyHeaderIdentityAdapter(
            user_repository=AsyncMock(),
            tenant_service=tenants,
        )
        with pytest.raises(InvalidTokenError, match="No recognized tenant role"):
            await adapter._sync_tenant(Principal("alice", "", "acme", []))
        tenants.sync_tenant_from_principal.assert_not_called()
        tenants.add_member.assert_not_called()

    async def test_membership_sync_failure_is_not_reported_as_success(self):
        from niuu.domain.models import Principal

        tenants = AsyncMock()
        tenants.add_member.side_effect = RuntimeError("database unavailable")
        adapter = EnvoyHeaderIdentityAdapter(
            user_repository=AsyncMock(),
            tenant_service=tenants,
        )
        with pytest.raises(RuntimeError, match="database unavailable"):
            await adapter._sync_tenant(Principal("alice", "", "acme", ["volundr:developer"]))

    async def test_validate_headers_falls_back_to_default_tenant_when_header_blank(self):
        user_repo = AsyncMock()
        adapter = EnvoyHeaderIdentityAdapter(
            user_repository=user_repo,
            default_tenant_id="default",
        )

        principal = await adapter.validate_headers(
            {
                "x-auth-user-id": "svc-user",
                "x-auth-email": "svc@example.com",
                "x-auth-tenant": "",
                "x-auth-roles": "",
            }
        )

        assert principal.user_id == "svc-user"
        assert principal.email == "svc@example.com"
        assert principal.tenant_id == "default"
        assert principal.roles == []


async def test_explicit_header_no_auth_accepts_anonymous_requests():
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from identity.adapters.http_auth import extract_principal
    from identity.adapters.identity import AllowAllHeaderAuthenticationAdapter

    app = FastAPI()
    app.state.identity = AllowAllHeaderAuthenticationAdapter(
        user_id_header="x-existing-verified-user", role_mapping={"admin": "volundr:admin"}
    )

    @app.get("/identity")
    async def who(principal: Principal = Depends(extract_principal)):
        return principal

    with TestClient(app) as client:
        response = client.get("/identity")
        assert response.status_code == 200
        assert response.json()["user_id"] == "dev-user"
        assert response.json()["roles"] == ["volundr:admin"]
        assert (
            client.get("/identity", headers={"x-auth-user-id": "attacker"}).json()
            == response.json()
        )
