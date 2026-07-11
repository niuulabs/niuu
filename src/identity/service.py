"""Tenant management service."""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from identity.models import (
    Principal,
    ProvisioningResult,
    QuotaCheck,
    StorageQuota,
    Tenant,
    TenantMembership,
    TenantRole,
    TenantTier,
    User,
    UserStatus,
)
from identity.ports import TenantRepository, UserRepository

if TYPE_CHECKING:
    from identity.ports import StoragePort

logger = logging.getLogger(__name__)


class TenantNotFoundError(Exception):
    """Raised when a tenant is not found."""


class TenantAlreadyExistsError(Exception):
    """Raised when a tenant path already exists."""


class TenantService:
    """Service for tenant hierarchy operations."""

    def __init__(
        self,
        tenant_repository: TenantRepository,
        user_repository: UserRepository,
    ) -> None:
        self._tenants = tenant_repository
        self._users = user_repository

    async def create_tenant(
        self,
        name: str,
        parent_id: str | None = None,
        tenant_id: str | None = None,
        tier: TenantTier = TenantTier.DEVELOPER,
        max_sessions: int = 5,
        max_storage_gb: int = 50,
    ) -> Tenant:
        """Create a new tenant in the hierarchy."""
        if tenant_id is None:
            tenant_id = str(uuid4())[:8]

        # Build the materialized path
        if parent_id is not None:
            parent = await self._tenants.get(parent_id)
            if parent is None:
                raise TenantNotFoundError(f"Parent tenant {parent_id} not found")
            path = f"{parent.path}.{tenant_id}"
        else:
            path = tenant_id

        existing = await self._tenants.get_by_path(path)
        if existing is not None:
            raise TenantAlreadyExistsError(f"Tenant path {path} already exists")

        tenant = Tenant(
            id=tenant_id,
            path=path,
            name=name,
            parent_id=parent_id,
            tier=tier,
            max_sessions=max_sessions,
            max_storage_gb=max_storage_gb,
        )
        return await self._tenants.create(tenant)

    async def get_tenant(self, tenant_id: str) -> Tenant:
        tenant = await self._tenants.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(f"Tenant {tenant_id} not found")
        return tenant

    async def list_tenants(self, parent_id: str | None = None) -> list[Tenant]:
        return await self._tenants.list(parent_id)

    async def list_users(self) -> list[User]:
        """List all users."""
        return await self._users.list()

    async def update_tenant(self, tenant: Tenant) -> Tenant:
        return await self._tenants.update(tenant)

    async def delete_tenant(self, tenant_id: str) -> bool:
        return await self._tenants.delete(tenant_id)

    async def check_quota(self, tenant_path: str, running_count_fn) -> QuotaCheck:
        """Check quotas along the full ancestor chain.

        Args:
            tenant_path: The tenant path to check.
            running_count_fn: Async callable(path) -> int that returns
                the number of running sessions matching the path prefix.
        """
        ancestors = await self._tenants.get_ancestors(tenant_path)
        for ancestor in ancestors:
            count = await running_count_fn(ancestor.path)
            if count >= ancestor.max_sessions:
                return QuotaCheck(
                    allowed=False,
                    tenant_id=ancestor.id,
                    max_sessions=ancestor.max_sessions,
                    current_sessions=count,
                    reason=(
                        f"Tenant '{ancestor.name}' at session limit"
                        f" ({count}/{ancestor.max_sessions})"
                    ),
                )

        # All ancestors within quota
        leaf = ancestors[-1] if ancestors else None
        if leaf is None:
            return QuotaCheck(
                allowed=True,
                tenant_id="",
                max_sessions=0,
                current_sessions=0,
            )
        count = await running_count_fn(leaf.path)
        return QuotaCheck(
            allowed=True,
            tenant_id=leaf.id,
            max_sessions=leaf.max_sessions,
            current_sessions=count,
        )

    async def add_member(
        self,
        tenant_id: str,
        user_id: str,
        role: TenantRole = TenantRole.DEVELOPER,
    ) -> TenantMembership:
        """Add or update a user's membership in a tenant."""
        tenant = await self._tenants.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(f"Tenant {tenant_id} not found")

        user = await self._users.get(user_id)
        if user is None:
            raise ValueError(f"User {user_id} not found")

        membership = TenantMembership(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
        )
        return await self._users.add_membership(membership)

    async def get_members(self, tenant_id: str) -> list[TenantMembership]:
        return await self._users.get_members(tenant_id)

    async def remove_member(self, tenant_id: str, user_id: str) -> bool:
        return await self._users.remove_membership(user_id, tenant_id)

    async def sync_tenant_from_principal(self, principal: Principal) -> Tenant:
        """Ensure the tenant from the principal's IDP claim exists in the DB.

        If the tenant does not exist, it is auto-created with defaults.
        """
        if not principal.tenant_id:
            return await self.ensure_default_tenant()

        existing = await self._tenants.get(principal.tenant_id)
        if existing is not None:
            return existing

        logger.info(
            "Auto-creating tenant %s from IDP claim for user %s",
            principal.tenant_id,
            principal.user_id,
        )
        return await self.create_tenant(
            name=principal.tenant_id,
            tenant_id=principal.tenant_id,
        )

    async def ensure_default_tenant(self) -> Tenant:
        """Ensure a default tenant exists for homelab/single-tenant mode."""
        existing = await self._tenants.get("default")
        if existing is not None:
            return existing

        logger.info("Creating default tenant for single-tenant mode")
        return await self.create_tenant(
            name="Default",
            tenant_id="default",
            tier=TenantTier.DEVELOPER,
            max_sessions=100,
            max_storage_gb=500,
        )

    async def reprovision_user(
        self,
        user_id: str,
        storage: StoragePort | None = None,
    ) -> ProvisioningResult:
        """Reprovision a single user's storage."""
        user = await self._users.get(user_id)
        if user is None:
            return ProvisioningResult(
                success=False,
                user_id=user_id,
                errors=[f"User {user_id} not found"],
            )

        updated = dataclasses.replace(user, status=UserStatus.PROVISIONING)
        await self._users.update(updated)

        try:
            home_pvc: str | None = None
            if storage is not None:
                pvc_ref = await storage.provision_user_storage(
                    user_id,
                    StorageQuota(),
                )
                home_pvc = pvc_ref.name

            active = dataclasses.replace(
                updated,
                status=UserStatus.ACTIVE,
                home_pvc=home_pvc or user.home_pvc,
                updated_at=datetime.now(UTC),
            )
            await self._users.update(active)
            logger.info("User %s reprovisioned successfully", user_id)
            return ProvisioningResult(
                success=True,
                user_id=user_id,
                home_pvc=active.home_pvc,
            )
        except Exception as exc:
            failed = dataclasses.replace(
                updated,
                status=UserStatus.FAILED,
                updated_at=datetime.now(UTC),
            )
            await self._users.update(failed)
            logger.error("Failed to reprovision user %s: %s", user_id, exc)
            return ProvisioningResult(
                success=False,
                user_id=user_id,
                errors=[str(exc)],
            )

    async def reprovision_tenant(
        self,
        tenant_id: str,
        storage: StoragePort | None = None,
    ) -> list[ProvisioningResult]:
        """Reprovision storage for all members of a tenant."""
        members = await self._users.get_members(tenant_id)
        results = []
        for membership in members:
            result = await self.reprovision_user(membership.user_id, storage)
            results.append(result)
        return results

    async def update_tenant_settings(
        self,
        tenant_id: str,
        *,
        max_sessions: int | None = None,
        max_storage_gb: int | None = None,
        tier: TenantTier | None = None,
    ) -> Tenant:
        """Update a tenant's configurable settings."""
        tenant = await self._tenants.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(f"Tenant {tenant_id} not found")

        replacements: dict = {}
        if max_sessions is not None:
            replacements["max_sessions"] = max_sessions
        if max_storage_gb is not None:
            replacements["max_storage_gb"] = max_storage_gb
        if tier is not None:
            replacements["tier"] = tier

        if not replacements:
            return tenant

        updated = dataclasses.replace(tenant, **replacements)
        return await self._tenants.update(updated)
