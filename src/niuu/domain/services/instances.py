"""Application service for the shared runtime instance registry."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.tags import matches_tags
from niuu.ports.instances import InstanceRepository


def _is_admin(principal: Principal) -> bool:
    return "volundr:admin" in principal.roles


class InstanceAccessError(PermissionError):
    """Raised when a principal cannot manage an instance."""


class InstanceValidationError(ValueError):
    """Raised when the instance payload is invalid."""


class InstanceService:
    """Tenant-aware registry service for runtime instances."""

    def __init__(self, repository: InstanceRepository) -> None:
        self._repository = repository

    async def list_visible(
        self,
        principal: Principal,
        *,
        kind: InstanceKind | None = None,
        enabled_only: bool = False,
        tags: list[str] | None = None,
        match: str = "all",
    ) -> list[RegisteredInstance]:
        instances = await self._repository.list_instances(kind)
        visible = [
            instance
            for instance in instances
            if self._is_visible_to(instance, principal)
            and (instance.enabled or not enabled_only)
            and matches_tags(instance.tags, tags, match)
        ]
        return sorted(
            visible,
            key=lambda instance: (
                0 if instance.is_default else 1,
                instance.name.lower(),
                instance.created_at,
            ),
        )

    async def get_visible(
        self,
        principal: Principal,
        instance_id: str,
    ) -> RegisteredInstance | None:
        instance = await self._repository.get_instance(instance_id)
        if instance is None or not self._is_visible_to(instance, principal):
            return None
        return instance

    async def create_instance(
        self,
        principal: Principal,
        *,
        kind: InstanceKind,
        slug: str,
        name: str,
        base_url: str,
        visibility: InstanceVisibility,
        enabled: bool = True,
        is_default: bool = False,
        config: dict | None = None,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        tags: list[str] | None = None,
    ) -> RegisteredInstance:
        owner_id, tenant_id = self._normalize_scope(
            principal,
            visibility=visibility,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        now = datetime.now(UTC)
        return await self._repository.save_instance(
            RegisteredInstance(
                id=str(uuid4()),
                kind=kind,
                slug=slug.strip(),
                name=name.strip(),
                base_url=base_url.strip().rstrip("/"),
                visibility=visibility,
                owner_id=owner_id,
                tenant_id=tenant_id,
                enabled=enabled,
                is_default=is_default,
                config=dict(config or {}),
                created_at=now,
                updated_at=now,
                tags=list(tags or []),
            )
        )

    async def update_instance(
        self,
        principal: Principal,
        instance_id: str,
        *,
        slug: str | None = None,
        name: str | None = None,
        base_url: str | None = None,
        visibility: InstanceVisibility | None = None,
        enabled: bool | None = None,
        is_default: bool | None = None,
        config: dict | None = None,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        tags: list[str] | None = None,
    ) -> RegisteredInstance:
        existing = await self._repository.get_instance(instance_id)
        if existing is None:
            raise LookupError(instance_id)
        self._require_manage_access(existing, principal)
        resolved_visibility = visibility or existing.visibility
        resolved_owner_id, resolved_tenant_id = self._normalize_scope(
            principal,
            visibility=resolved_visibility,
            owner_id=owner_id if visibility is not None else existing.owner_id,
            tenant_id=tenant_id if visibility is not None else existing.tenant_id,
        )
        updated = replace(
            existing,
            slug=slug.strip() if slug is not None else existing.slug,
            name=name.strip() if name is not None else existing.name,
            base_url=base_url.strip().rstrip("/") if base_url is not None else existing.base_url,
            visibility=resolved_visibility,
            owner_id=resolved_owner_id,
            tenant_id=resolved_tenant_id,
            enabled=enabled if enabled is not None else existing.enabled,
            is_default=is_default if is_default is not None else existing.is_default,
            config=dict(config) if config is not None else existing.config,
            tags=list(tags) if tags is not None else existing.tags,
            updated_at=datetime.now(UTC),
        )
        return await self._repository.save_instance(updated)

    async def delete_instance(self, principal: Principal, instance_id: str) -> None:
        existing = await self._repository.get_instance(instance_id)
        if existing is None:
            return
        self._require_manage_access(existing, principal)
        await self._repository.delete_instance(instance_id)

    async def upsert_seed_instance(
        self,
        *,
        kind: InstanceKind,
        slug: str,
        name: str,
        base_url: str,
        visibility: InstanceVisibility,
        enabled: bool = True,
        is_default: bool = False,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        config: dict | None = None,
        tags: list[str] | None = None,
        instance_id: str | None = None,
    ) -> RegisteredInstance:
        slug = slug.strip()
        base_url = base_url.strip().rstrip("/")
        existing = await self._find_seed_match(kind, slug, visibility, owner_id, tenant_id)
        if existing is not None:
            seeded = replace(
                existing,
                name=name.strip(),
                base_url=base_url,
                enabled=enabled,
                is_default=is_default,
                config=dict(config or {}),
                tags=list(tags) if tags is not None else existing.tags,
                updated_at=datetime.now(UTC),
            )
            return await self._repository.save_instance(seeded)

        now = datetime.now(UTC)
        return await self._repository.save_instance(
            RegisteredInstance(
                id=instance_id or str(uuid4()),
                kind=kind,
                slug=slug,
                name=name.strip(),
                base_url=base_url,
                visibility=visibility,
                owner_id=owner_id,
                tenant_id=tenant_id,
                enabled=enabled,
                is_default=is_default,
                config=dict(config or {}),
                created_at=now,
                updated_at=now,
                tags=list(tags or []),
            )
        )

    async def _find_seed_match(
        self,
        kind: InstanceKind,
        slug: str,
        visibility: InstanceVisibility,
        owner_id: str | None,
        tenant_id: str | None,
    ) -> RegisteredInstance | None:
        for instance in await self._repository.list_instances(kind):
            if (
                instance.slug == slug
                and instance.visibility == visibility
                and instance.owner_id == owner_id
                and instance.tenant_id == tenant_id
            ):
                return instance
        return None

    def _require_manage_access(self, instance: RegisteredInstance, principal: Principal) -> None:
        if instance.visibility == InstanceVisibility.USER:
            if instance.owner_id == principal.user_id or _is_admin(principal):
                return
        elif instance.visibility == InstanceVisibility.TENANT:
            if instance.tenant_id == principal.tenant_id or _is_admin(principal):
                return
        elif _is_admin(principal):
            return
        raise InstanceAccessError(f"Principal cannot manage instance {instance.id}")

    def _is_visible_to(self, instance: RegisteredInstance, principal: Principal) -> bool:
        if instance.visibility == InstanceVisibility.SYSTEM:
            return True
        if instance.visibility == InstanceVisibility.TENANT:
            return bool(instance.tenant_id) and instance.tenant_id == principal.tenant_id
        return bool(instance.owner_id) and instance.owner_id == principal.user_id

    def _normalize_scope(
        self,
        principal: Principal,
        *,
        visibility: InstanceVisibility,
        owner_id: str | None,
        tenant_id: str | None,
    ) -> tuple[str | None, str | None]:
        if visibility == InstanceVisibility.SYSTEM:
            if not _is_admin(principal):
                raise InstanceAccessError("Only admins may register system instances")
            return None, None

        if visibility == InstanceVisibility.TENANT:
            resolved_tenant = (tenant_id or principal.tenant_id).strip()
            if not resolved_tenant:
                raise InstanceValidationError("tenant visibility requires a tenant_id")
            if resolved_tenant != principal.tenant_id and not _is_admin(principal):
                raise InstanceAccessError("Cannot register instances for another tenant")
            return None, resolved_tenant

        resolved_owner = (owner_id or principal.user_id).strip()
        if not resolved_owner:
            raise InstanceValidationError("user visibility requires an owner_id")
        if resolved_owner != principal.user_id and not _is_admin(principal):
            raise InstanceAccessError("Cannot register instances for another user")
        return resolved_owner, None
