"""Tests for the shared identity application service."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from volundr.domain.models import TenantRole, TenantTier
from volundr.domain.services.identity import (
    IdentityService,
    TenantCreateCommand,
    TenantUpdateCommand,
)
from volundr.domain.services.tenant import TenantService


@pytest.mark.asyncio
async def test_create_tenant_normalizes_command_into_tenant_service_call() -> None:
    tenant_service = AsyncMock(spec=TenantService)
    service = IdentityService(tenant_service)

    await service.create_tenant(
        TenantCreateCommand(
            name="Acme",
            tenant_id="acme",
            parent_id="root",
            tier=TenantTier.ENTERPRISE,
            max_sessions=12,
            max_storage_gb=250,
        )
    )

    tenant_service.create_tenant.assert_awaited_once_with(
        name="Acme",
        parent_id="root",
        tenant_id="acme",
        tier=TenantTier.ENTERPRISE,
        max_sessions=12,
        max_storage_gb=250,
    )


@pytest.mark.asyncio
async def test_update_tenant_coerces_string_tier_values() -> None:
    tenant_service = AsyncMock(spec=TenantService)
    service = IdentityService(tenant_service)

    await service.update_tenant(
        "tenant-1",
        TenantUpdateCommand(max_sessions=9, tier="team"),
    )

    tenant_service.update_tenant_settings.assert_awaited_once_with(
        "tenant-1",
        max_sessions=9,
        max_storage_gb=None,
        tier=TenantTier.TEAM,
    )


@pytest.mark.asyncio
async def test_add_member_delegates_to_tenant_service() -> None:
    tenant_service = AsyncMock(spec=TenantService)
    service = IdentityService(tenant_service)

    await service.add_member("tenant-1", user_id="user-1", role=TenantRole.ADMIN)

    tenant_service.add_member.assert_awaited_once_with(
        tenant_id="tenant-1",
        user_id="user-1",
        role=TenantRole.ADMIN,
    )
