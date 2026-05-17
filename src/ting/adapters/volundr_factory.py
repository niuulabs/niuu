"""Factory for resolving per-owner VolundrHTTPAdapter instances.

Supports multiple Volundr connections per user (multi-cluster).  Each enabled
CODE_FORGE IntegrationConnection becomes a separate VolundrHTTPAdapter.
A global fallback URL is used when the user has no per-user connections.
"""

from __future__ import annotations

import logging

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    IntegrationType,
    Principal,
)
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.instances import InstanceRepository
from niuu.ports.integrations import IntegrationRepository
from ting.adapters.volundr_http import VolundrHTTPAdapter
from ting.ports.volundr import VolundrPort

logger = logging.getLogger(__name__)

DEFAULT_VOLUNDR_URL = "http://volundr:8000"


class LocalVolundrAdapterFactory:
    """Volundr adapter factory for mini/local mode.

    Always returns a single VolundrHTTPAdapter pointing at the local
    server with no PAT required.  Same interface as VolundrAdapterFactory
    so all callers (dispatch, activity subscriber, review engine) work
    without fallback logic.
    """

    def __init__(self, url: str) -> None:
        self._adapter = VolundrHTTPAdapter(base_url=url, name="local")

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        return [self._adapter]

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        return self._adapter

    async def for_principal(self, principal: Principal) -> list[VolundrPort]:
        return [self._adapter]

    async def primary_for_principal(self, principal: Principal) -> VolundrPort | None:
        return self._adapter


class VolundrAdapterFactory:
    """Resolves VolundrHTTPAdapter instances for a specific owner.

    Returns only **authenticated** adapters backed by a stored credential.
    When no per-user CODE_FORGE connections exist, returns an empty list
    (or ``None`` for the primary helper).  Callers must handle the missing
    case explicitly — silent fallback to an unauthenticated adapter is
    intentionally removed.
    """

    def __init__(
        self,
        integration_repo: IntegrationRepository,
        credential_store: CredentialStorePort,
        instance_repo: InstanceRepository | None = None,
        *,
        allow_unauthenticated: bool = False,
    ) -> None:
        self._integration_repo = integration_repo
        self._credential_store = credential_store
        self._instance_repo = instance_repo
        self._allow_unauthenticated = allow_unauthenticated

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        """Return all authenticated VolundrHTTPAdapter instances for *owner_id*.

        Returns an empty list when the user has no enabled CODE_FORGE
        connections with valid credentials.
        """
        return await self._resolve_connections(owner_id)

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        """Return the first (primary) authenticated adapter, or ``None``."""
        adapters = await self._resolve_connections(owner_id)
        if adapters:
            return adapters[0]
        return None

    async def for_principal(self, principal: Principal) -> list[VolundrPort]:
        return await self._resolve_connections(principal.user_id, principal=principal)

    async def primary_for_principal(self, principal: Principal) -> VolundrPort | None:
        adapters = await self._resolve_connections(principal.user_id, principal=principal)
        if adapters:
            return adapters[0]
        return None

    async def _resolve_connections(
        self,
        owner_id: str,
        *,
        principal: Principal | None = None,
    ) -> list[VolundrPort]:
        """Resolve registry-backed and legacy connection-backed adapters."""
        adapters = await self._resolve_registered_instances(owner_id, principal=principal)
        if adapters:
            return adapters

        connections = await self._integration_repo.list_connections(
            owner_id,
            integration_type=IntegrationType.CODE_FORGE,
        )
        adapters = []
        for conn in connections:
            if not conn.enabled:
                continue
            try:
                cred = await self._credential_store.get_value(
                    "user", owner_id, conn.credential_name
                )
                token = cred.get("token") if cred is not None else None
                if not token and not self._allow_unauthenticated:
                    continue
                adapter_name = conn.config.get("name", "") or conn.slug or conn.id
                adapters.append(
                    VolundrHTTPAdapter(
                        base_url=conn.config.get("url", DEFAULT_VOLUNDR_URL),
                        api_key=token,
                        name=adapter_name,
                        target_id=conn.id,
                    )
                )
            except Exception:
                logger.error(
                    "Failed to create Volundr adapter for connection %s (owner=%s)",
                    conn.id,
                    owner_id,
                    exc_info=True,
                )
        return adapters

    async def _resolve_registered_instances(
        self,
        owner_id: str,
        *,
        principal: Principal | None = None,
    ) -> list[VolundrPort]:
        if self._instance_repo is None:
            return []

        adapters: list[VolundrPort] = []
        try:
            instances = await self._instance_repo.list_instances(InstanceKind.VOLUNDR)
        except Exception:
            logger.error("Failed to load registered Volundr instances", exc_info=True)
            return []

        for instance in sorted(
            instances,
            key=lambda item: (0 if item.is_default else 1, item.name.lower(), item.created_at),
        ):
            if not instance.enabled:
                continue
            if instance.visibility == InstanceVisibility.USER:
                if instance.owner_id != owner_id:
                    continue
            elif instance.visibility == InstanceVisibility.TENANT:
                if principal is None or not principal.tenant_id:
                    continue
                if instance.tenant_id != principal.tenant_id:
                    continue
            elif instance.visibility != InstanceVisibility.SYSTEM:
                continue
            try:
                token = await self._resolve_instance_token(instance, owner_id)
                adapters.append(
                    VolundrHTTPAdapter(
                        base_url=instance.base_url,
                        api_key=token,
                        name=instance.name,
                        target_id=instance.id,
                    )
                )
            except Exception:
                logger.error(
                    "Failed to create Volundr adapter for instance %s (owner=%s)",
                    instance.id,
                    owner_id,
                    exc_info=True,
                )
        return adapters

    async def _resolve_instance_token(self, instance, owner_id: str) -> str | None:
        credential_name = str(instance.config.get("credential_name") or "").strip()
        if not credential_name:
            return None
        credential = await self._credential_store.get_value("user", owner_id, credential_name)
        if credential is None:
            return None
        token = credential.get("token")
        return str(token).strip() if token else None
