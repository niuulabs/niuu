"""Port for realm governance persistence operations.

A realm carries a Valkyrie's build capability, trust level, and per-Valkyrie
build config. Trust grants and capabilities are scoped to a realm.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from niuu.domain.models import Capability, Realm, TrustGrant


class RealmRepository(ABC):
    """Port for realm, trust-grant, and capability persistence operations."""

    @abstractmethod
    async def list_realms(self) -> list[Realm]:
        """List all realms."""

    @abstractmethod
    async def get_realm(self, realm_ref: UUID | str) -> Realm | None:
        """Retrieve a realm by id (UUID) or slug (str)."""

    @abstractmethod
    async def save_realm(self, realm: Realm) -> Realm:
        """Create or update a realm (upsert by id)."""

    @abstractmethod
    async def list_trust_grants(self, realm_id: UUID) -> list[TrustGrant]:
        """List all trust grants for a realm."""

    @abstractmethod
    async def save_trust_grant(self, grant: TrustGrant) -> TrustGrant:
        """Create or update a trust grant (upsert by id)."""

    @abstractmethod
    async def list_capabilities(self, realm_id: UUID) -> list[Capability]:
        """List all capabilities for a realm."""

    @abstractmethod
    async def save_capability(self, capability: Capability) -> Capability:
        """Create or update a capability (upsert by id)."""

    @abstractmethod
    async def upsert_capability(self, capability: Capability) -> Capability:
        """Insert or update a capability keyed on (realm_id, name)."""
