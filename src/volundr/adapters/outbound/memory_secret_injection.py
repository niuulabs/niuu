"""In-memory SecretInjection adapter.

Used as a default for development and testing when no external
secret injection backend (Infisical CSI, Vault CSI) is available.
Returns empty PodSpecAdditions so sessions run without CSI volumes.
"""

from __future__ import annotations

import logging

from volundr.domain.models import CredentialMapping, PodSpecAdditions
from volundr.domain.ports import SecretInjectionPort

logger = logging.getLogger(__name__)


class InMemorySecretInjectionAdapter(SecretInjectionPort):
    """In-memory secret injection adapter for development.

    Returns empty pod spec additions. Tracks provisioned users
    in a plain set for testing purposes.

    Constructor accepts **kwargs for dynamic adapter compatibility.
    """

    def __init__(self, **_extra: object) -> None:
        self._provisioned_users: set[str] = set()

    async def pod_spec_additions(
        self,
        user_id: str,
        session_id: str,
    ) -> PodSpecAdditions:
        """Return empty additions in dev mode."""
        return PodSpecAdditions()

    async def ensure_secret_provider_class(
        self,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """No-op in dev mode."""

    async def provision_user(self, user_id: str) -> None:
        """Track user as provisioned."""
        self._provisioned_users.add(user_id)
        logger.debug("Provisioned user %s (in-memory)", user_id)

    async def deprovision_user(self, user_id: str) -> None:
        """Remove user from provisioned set."""
        self._provisioned_users.discard(user_id)
        logger.debug("Deprovisioned user %s (in-memory)", user_id)
