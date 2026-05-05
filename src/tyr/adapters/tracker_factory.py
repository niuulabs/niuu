"""Factory for resolving per-owner TrackerPort adapter instances."""

from __future__ import annotations

import logging
from typing import Any

from niuu.domain.models import IntegrationType
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository
from niuu.utils import import_class
from tyr.adapters.native import NativeTrackerAdapter
from tyr.ports.tracker import TrackerPort

logger = logging.getLogger(__name__)


class TrackerAdapterFactory:
    """Resolves tracker adapters for a specific owner from stored credentials.

    The pool is injected into every adapter so that adapters that need local
    postgres storage (e.g. LinearAdapter's raid_progress table) receive it
    without exposing it as a user-config kwarg.
    """

    def __init__(
        self,
        integration_repo: IntegrationRepository,
        credential_store: CredentialStorePort,
        pool: Any | None = None,
    ) -> None:
        self._integration_repo = integration_repo
        self._credential_store = credential_store
        self._pool = pool

    async def for_owner(self, owner_id: str) -> list[TrackerPort]:
        """Return all enabled TrackerPort adapters for the owner.

        Uses the dynamic adapter pattern: import_class(conn.adapter)(**kwargs).
        The pool is injected alongside the credential/config kwargs so adapters
        that require local postgres storage (LinearAdapter) receive it.
        """
        connections = await self._integration_repo.list_connections(
            owner_id,
            integration_type=IntegrationType.ISSUE_TRACKER,
        )
        adapters: list[TrackerPort] = []
        for conn in connections:
            if not conn.enabled:
                continue
            # Skip adapters from other services (e.g. volundr's LinearAdapter)
            if conn.adapter.startswith("volundr."):
                continue
            try:
                cred = await self._credential_store.get_value(
                    "user",
                    owner_id,
                    conn.credential_name,
                )
                if cred is None:
                    continue

                cls = import_class(conn.adapter)
                kwargs = {**cred, **conn.config}
                if self._pool is not None:
                    kwargs["pool"] = self._pool
                adapters.append(cls(**kwargs))
            except (ImportError, TypeError, ValueError, AttributeError) as exc:
                logger.error(
                    "Failed to create tracker adapter for connection %s: %s",
                    conn.id,
                    exc,
                )
            except Exception:
                logger.error(
                    "Unexpected error creating tracker adapter for connection %s",
                    conn.id,
                    exc_info=True,
                )
        if not adapters and self._pool is not None:
            logger.info(
                "No tracker integrations configured for owner %s; "
                "using NativeTrackerAdapter fallback",
                owner_id,
            )
            adapters.append(NativeTrackerAdapter(pool=self._pool))
        return adapters
