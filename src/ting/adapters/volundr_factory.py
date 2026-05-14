"""Factory for resolving per-owner VolundrHTTPAdapter instances.

Supports multiple Volundr connections per user (multi-cluster).  Each enabled
CODE_FORGE IntegrationConnection becomes a separate VolundrHTTPAdapter.
A global fallback URL is used when the user has no per-user connections.
"""

from __future__ import annotations

import logging

from niuu.domain.models import IntegrationType
from niuu.ports.credentials import CredentialStorePort
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
        *,
        allow_unauthenticated: bool = False,
    ) -> None:
        self._integration_repo = integration_repo
        self._credential_store = credential_store
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

    async def _resolve_connections(self, owner_id: str) -> list[VolundrPort]:
        """Resolve all enabled CODE_FORGE connections into adapters."""
        connections = await self._integration_repo.list_connections(
            owner_id,
            integration_type=IntegrationType.CODE_FORGE,
        )
        adapters: list[VolundrPort] = []
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
