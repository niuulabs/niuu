"""Application service for the shared runtime instance registry."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from identity.models import Resource
from identity.ports import AuthorizationPort
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.tags import matches_tags
from niuu.domain.tls_fingerprint import normalize_tls_fingerprint
from niuu.domain.transport_security import (
    configured_dial_urls,
    insecure_transport_reason,
    normalize_allow_plaintext,
)
from niuu.ports.instances import InstanceRepository


def _is_admin(principal: Principal) -> bool:
    return "volundr:admin" in principal.roles


class InstanceAccessError(PermissionError):
    """Raised when a principal cannot manage an instance."""


class InstanceValidationError(ValueError):
    """Raised when the instance payload is invalid."""


class InstanceTransportSecurityError(InstanceValidationError):
    """Raised when a remote instance's transport does not meet the LAN policy.

    See the "LAN transport" owner decision: Tailscale/an already-encrypted
    network first, a pinned self-signed certificate second, plaintext only as
    an explicit per-instance opt-in. A distinct subclass so the REST layer can
    map this to 422 (a malformed request) rather than the 403 used for the
    other ``InstanceValidationError`` cases (a request that is well-formed but
    not permitted).

    This write-time check is a fail-fast convenience, not the enforcement
    boundary: a row that predates this validation (or was written before a
    ``ravn_base_url`` was added) is still stopped at every outbound call by
    ``niuu.adapters.outbound.guild_transport`` — see that module's own
    enforcement of the same policy against whichever URL is actually dialled.
    """


def _require_secure_transport(base_url: str, config: dict) -> None:
    """Enforce https:// for every URL this instance may be dialled on.

    A write-time fail-fast check covering both ``base_url`` and a configured
    ``ravn_base_url`` — see ``InstanceTransportSecurityError`` for why this
    alone is not the enforcement boundary. A configured ``tls_fingerprint``
    is validated here too (well-formed, and only meaningful over https) so a
    malformed or unusable pin is rejected at registration, not discovered at
    the first outbound call.
    """
    try:
        allow_plaintext = normalize_allow_plaintext(config)
    except ValueError as exc:
        raise InstanceTransportSecurityError(str(exc)) from exc
    fingerprint = config.get("tls_fingerprint")
    if fingerprint is not None:
        try:
            normalize_tls_fingerprint(str(fingerprint))
        except ValueError as exc:
            raise InstanceTransportSecurityError(str(exc)) from exc
    for url in configured_dial_urls(base_url, config):
        reason = insecure_transport_reason(url, allow_plaintext=allow_plaintext)
        if reason:
            raise InstanceTransportSecurityError(reason)
        if fingerprint is not None and urlsplit(url).scheme != "https":
            raise InstanceTransportSecurityError(
                f"{url}: config.tls_fingerprint requires https:// on every "
                "URL this instance may be dialled on"
            )


def _touches_transport_fields(base_url: str | None, config: dict | None) -> bool:
    """Whether an ``update_instance`` call is changing where/how this instance is dialled."""
    return base_url is not None or config is not None


class InstanceService:
    """Tenant-aware registry service for runtime instances."""

    def __init__(self, repository: InstanceRepository, *, authorization: AuthorizationPort) -> None:
        self._repository = repository
        self._authorization = authorization

    @staticmethod
    def _resource(instance):
        return Resource(
            "instance",
            instance.id,
            {
                "owner_id": instance.owner_id or "",
                "tenant_id": instance.tenant_id or "",
                "visibility": instance.visibility.value,
            },
        )

    async def _check(self, principal, action, instance):
        if not await self._authorization.is_allowed(principal, action, self._resource(instance)):
            raise InstanceAccessError("Instance operation denied")

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
        allowed = await self._authorization.filter_allowed(
            principal, "list", [self._resource(i) for i in visible]
        )
        allowed_ids = {r.id for r in allowed}
        return sorted(
            [i for i in visible if i.id in allowed_ids],
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
        if not await self._authorization.is_allowed(principal, "read", self._resource(instance)):
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
        normalized_base_url = base_url.strip().rstrip("/")
        normalized_config = dict(config or {})
        _require_secure_transport(normalized_base_url, normalized_config)
        instance = RegisteredInstance(
            id=str(uuid4()),
            kind=kind,
            slug=slug.strip(),
            name=name.strip(),
            base_url=normalized_base_url,
            visibility=visibility,
            owner_id=owner_id,
            tenant_id=tenant_id,
            enabled=enabled,
            is_default=is_default,
            config=normalized_config,
            created_at=now,
            updated_at=now,
            tags=list(tags or []),
        )

        await self._check(principal, "create", instance)
        return await self._repository.save_instance(instance)

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
        await self._check(principal, "update", existing)
        resolved_visibility = visibility or existing.visibility
        resolved_owner_id, resolved_tenant_id = self._normalize_scope(
            principal,
            visibility=resolved_visibility,
            owner_id=owner_id if visibility is not None else existing.owner_id,
            tenant_id=tenant_id if visibility is not None else existing.tenant_id,
        )
        resolved_base_url = (
            base_url.strip().rstrip("/") if base_url is not None else existing.base_url
        )
        resolved_config = dict(config) if config is not None else existing.config
        # A row can predate this validation (or predate ravn_base_url), so an
        # update that does not touch base_url/config, or that disables the
        # instance, must not be trapped behind a security check it isn't
        # asking to change — the caller may be trying to turn the insecure
        # instance OFF. Call-time enforcement (guild_transport.py) is the
        # real boundary regardless: it re-checks every dialled URL on every
        # outbound call, so a legacy row is never silently used insecurely
        # even when its own update sails through here.
        #
        # Re-enabling is treated the same as touching the transport fields:
        # PATCH {base_url: "http://x", enabled: false} correctly skips the
        # check (it is disabling), but a later, separate
        # PATCH {enabled: true} on that same row — base_url/config untouched
        # by *this* call — must not silently re-enable an instance this
        # service already knows is insecure just because neither field was
        # part of this particular request.
        resulting_enabled = enabled if enabled is not None else existing.enabled
        re_enabling = enabled is True and not existing.enabled
        if resulting_enabled and (_touches_transport_fields(base_url, config) or re_enabling):
            _require_secure_transport(resolved_base_url, resolved_config)
        updated = replace(
            existing,
            slug=slug.strip() if slug is not None else existing.slug,
            name=name.strip() if name is not None else existing.name,
            base_url=resolved_base_url,
            visibility=resolved_visibility,
            owner_id=resolved_owner_id,
            tenant_id=resolved_tenant_id,
            enabled=enabled if enabled is not None else existing.enabled,
            is_default=is_default if is_default is not None else existing.is_default,
            config=resolved_config,
            tags=list(tags) if tags is not None else existing.tags,
            updated_at=datetime.now(UTC),
        )
        await self._check(principal, "update", updated)
        return await self._repository.save_instance(updated)

    async def delete_instance(self, principal: Principal, instance_id: str) -> None:
        existing = await self._repository.get_instance(instance_id)
        if existing is None:
            return
        self._require_manage_access(existing, principal)
        await self._check(principal, "delete", existing)
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
        normalized_config = dict(config or {})
        _require_secure_transport(base_url, normalized_config)
        existing = await self._find_seed_match(kind, slug, visibility, owner_id, tenant_id)
        if existing is not None:
            seeded = replace(
                existing,
                name=name.strip(),
                base_url=base_url,
                enabled=enabled,
                is_default=is_default,
                config=normalized_config,
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
                config=normalized_config,
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
        return resolved_owner, principal.tenant_id
