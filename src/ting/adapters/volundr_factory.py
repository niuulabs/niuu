"""Factory for resolving per-owner VolundrHTTPAdapter instances."""

from __future__ import annotations

import logging

from niuu.domain.models import Principal, RegisteredInstance
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.http_auth import HttpAuthPort
from ting.adapters.guild_instances import GuildInstanceRegistryClient
from ting.adapters.volundr_http import VolundrHTTPAdapter
from ting.ports.volundr import VolundrPort

logger = logging.getLogger(__name__)


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

    async def for_connection(self, owner_id: str, connection_id: str) -> VolundrPort | None:
        return self._adapter


class VolundrAdapterFactory:
    """Resolve VolundrHTTPAdapter instances from Guild's shared registry."""

    def __init__(
        self,
        registry: GuildInstanceRegistryClient,
        credential_store: CredentialStorePort,
        *,
        allow_unauthenticated: bool = False,
        target_auth: HttpAuthPort | None = None,
    ) -> None:
        self._registry = registry
        self._credential_store = credential_store
        self._allow_unauthenticated = allow_unauthenticated
        self._target_auth = target_auth

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

    async def for_connection(self, owner_id: str, connection_id: str) -> VolundrPort | None:
        """Resolve the owner's adapter for a specific connection.

        Matches by target id or name — the same identifiers the launch path
        accepts — so a campaign resolves to the Volundr instance its session
        actually lives on. Returns None when the connection is gone.
        """
        wanted = str(connection_id or "").strip()
        if not wanted:
            return None
        for adapter in await self._resolve_connections(owner_id):
            if wanted in {adapter.target_id, adapter.name}:
                return adapter
        return None

    async def _resolve_connections(
        self,
        owner_id: str,
        *,
        principal: Principal | None = None,
    ) -> list[VolundrPort]:
        """Resolve Guild-registered Volundr adapters."""
        if principal is None:
            principal = Principal(
                user_id=owner_id,
                email="",
                tenant_id="default",
                roles=["volundr:developer"],
            )

        adapters: list[VolundrPort] = []
        try:
            instances = await self._registry.list_volundr_targets(principal)
        except Exception:
            logger.error("Failed to load Volundr targets from Guild", exc_info=True)
            return []

        for instance in sorted(
            instances,
            key=lambda item: (0 if item.is_default else 1, item.name.lower(), item.created_at),
        ):
            try:
                token = await self._resolve_instance_token(instance, principal.user_id)
                credential_name = str(instance.config.get("credential_name") or "").strip()
                if credential_name and not token and not self._allow_unauthenticated:
                    logger.info(
                        "Skipping Guild Volundr target %s without usable credential",
                        instance.id,
                    )
                    continue
                adapters.append(
                    VolundrHTTPAdapter(
                        base_url=instance.base_url,
                        api_key=token,
                        name=instance.name,
                        target_id=instance.id,
                        tags=instance.tags,
                        auth=self._target_auth,
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

    async def _resolve_instance_token(
        self,
        instance: RegisteredInstance,
        owner_id: str,
    ) -> str | None:
        credential_name = str(instance.config.get("credential_name") or "").strip()
        if not credential_name:
            return None
        credential = await self._credential_store.get_value("user", owner_id, credential_name)
        if credential is None:
            return None
        token = credential.get("token")
        return str(token).strip() if token else None
