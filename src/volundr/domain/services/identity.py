"""Shared identity application service used by canonical and legacy routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from volundr.domain.models import Principal, TenantRole, TenantTier
from volundr.domain.services.tenant import TenantService

if TYPE_CHECKING:
    from volundr.domain.models import ProvisioningResult, Tenant, TenantMembership, User
    from volundr.domain.ports import StoragePort


@dataclass(frozen=True)
class TenantCreateCommand:
    """Normalized tenant creation input shared across route mounts."""

    name: str
    tenant_id: str | None = None
    parent_id: str | None = None
    tier: TenantTier = TenantTier.DEVELOPER
    max_sessions: int = 5
    max_storage_gb: int = 50


@dataclass(frozen=True)
class TenantUpdateCommand:
    """Normalized tenant update input shared across route mounts."""

    max_sessions: int | None = None
    max_storage_gb: int | None = None
    tier: TenantTier | str | None = None


class IdentityService:
    """Thin application service that centralizes identity route behavior."""

    def __init__(self, tenant_service: TenantService) -> None:
        self._tenant_service = tenant_service

    async def current_principal(self, principal: Principal) -> Principal:
        return principal

    async def list_users(self) -> list[User]:
        return await self._tenant_service.list_users()

    async def reprovision_user(
        self,
        user_id: str,
        *,
        storage: StoragePort | None = None,
    ) -> ProvisioningResult:
        return await self._tenant_service.reprovision_user(user_id, storage=storage)

    async def list_tenants(self, parent_id: str | None = None) -> list[Tenant]:
        return await self._tenant_service.list_tenants(parent_id)

    async def create_tenant(self, command: TenantCreateCommand) -> Tenant:
        return await self._tenant_service.create_tenant(
            name=command.name,
            parent_id=command.parent_id,
            tenant_id=command.tenant_id,
            tier=command.tier,
            max_sessions=command.max_sessions,
            max_storage_gb=command.max_storage_gb,
        )

    async def get_tenant(self, tenant_id: str) -> Tenant:
        return await self._tenant_service.get_tenant(tenant_id)

    async def update_tenant(self, tenant_id: str, command: TenantUpdateCommand) -> Tenant:
        tier = command.tier
        if isinstance(tier, str):
            tier = TenantTier(tier)
        return await self._tenant_service.update_tenant_settings(
            tenant_id,
            max_sessions=command.max_sessions,
            max_storage_gb=command.max_storage_gb,
            tier=tier,
        )

    async def delete_tenant(self, tenant_id: str) -> bool:
        return await self._tenant_service.delete_tenant(tenant_id)

    async def list_members(self, tenant_id: str) -> list[TenantMembership]:
        return await self._tenant_service.get_members(tenant_id)

    async def add_member(
        self,
        tenant_id: str,
        *,
        user_id: str,
        role: TenantRole = TenantRole.DEVELOPER,
    ) -> TenantMembership:
        return await self._tenant_service.add_member(
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
        )

    async def remove_member(self, tenant_id: str, user_id: str) -> bool:
        return await self._tenant_service.remove_member(tenant_id, user_id)

    async def reprovision_tenant(
        self,
        tenant_id: str,
        *,
        storage: StoragePort | None = None,
    ) -> list[ProvisioningResult]:
        return await self._tenant_service.reprovision_tenant(tenant_id, storage=storage)
