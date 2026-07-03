"""Domain service for realm governance — realms, trust grants, capabilities.

A realm is a Valkyrie's domain. This service is the single business entry point
for creating realms, granting trust, recording capabilities, and — the load-
bearing method for the tool-build spine — resolving the effective ``build`` trust
grant that P3/P4 read to decide which Ting workflow a Valkyrie may commission.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.domain.models import Capability, Realm, TrustGrant
from niuu.ports.realm_repository import RealmRepository

logger = logging.getLogger(__name__)

BUILD_ACTION_CLASS = "build"


class RealmService:
    """Service for realm, trust-grant, and capability lifecycle management."""

    def __init__(self, repo: RealmRepository) -> None:
        self._repo = repo

    async def list_realms(self) -> list[Realm]:
        """List all realms."""
        return await self._repo.list_realms()

    async def get_realm(self, realm_ref: UUID | str) -> Realm | None:
        """Retrieve a realm by id or slug."""
        return await self._repo.get_realm(realm_ref)

    async def create_realm(
        self,
        slug: str,
        name: str,
        *,
        sleipnir_domain: str | None = None,
        owner_id: str | None = None,
        instance_id: str | None = None,
        autonomy_profile: str = "balanced",
    ) -> Realm:
        """Create a new realm."""
        now = datetime.now(UTC)
        realm = Realm(
            id=uuid4(),
            slug=slug,
            name=name,
            sleipnir_domain=sleipnir_domain,
            owner_id=owner_id,
            instance_id=instance_id,
            autonomy_profile=autonomy_profile,
            created_at=now,
            updated_at=now,
        )
        saved = await self._repo.save_realm(realm)
        logger.info("realm created: id=%s slug=%s", saved.id, saved.slug)
        return saved

    async def list_trust_grants(self, realm_id: UUID) -> list[TrustGrant]:
        """List all trust grants for a realm."""
        return await self._repo.list_trust_grants(realm_id)

    async def grant_trust(
        self,
        realm_id: UUID,
        action_class: str,
        *,
        target: str = "*",
        level: int = 0,
        limits: dict | None = None,
        granted_by: str | None = None,
    ) -> TrustGrant:
        """Grant a realm's Valkyrie permission for an action class."""
        grant = TrustGrant(
            id=uuid4(),
            realm_id=realm_id,
            action_class=action_class,
            target=target,
            level=level,
            limits=limits or {},
            granted_by=granted_by,
            granted_at=datetime.now(UTC),
        )
        saved = await self._repo.save_trust_grant(grant)
        logger.info(
            "trust granted: realm=%s action=%s level=%s",
            realm_id,
            action_class,
            level,
        )
        return saved

    async def list_capabilities(self, realm_id: UUID) -> list[Capability]:
        """List all capabilities for a realm."""
        return await self._repo.list_capabilities(realm_id)

    async def record_capability(
        self,
        realm_id: UUID,
        name: str,
        kind: str,
        *,
        status: str = "gap",
        trust_level: int = 0,
        mimir_page_path: str | None = None,
        notes: str | None = None,
    ) -> Capability:
        """Record (upsert) a capability for a realm, keyed on (realm_id, name)."""
        now = datetime.now(UTC)
        capability = Capability(
            id=uuid4(),
            realm_id=realm_id,
            name=name,
            kind=kind,
            status=status,
            trust_level=trust_level,
            mimir_page_path=mimir_page_path,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        saved = await self._repo.upsert_capability(capability)
        logger.info(
            "capability recorded: realm=%s name=%s status=%s",
            realm_id,
            name,
            status,
        )
        return saved

    async def resolve_build_grant(self, realm_slug_or_valkyrie_id: UUID | str) -> TrustGrant | None:
        """Resolve the effective ``build`` trust grant for a realm.

        Accepts a realm slug (or the Valkyrie id, which is the realm slug) or a
        realm UUID. Returns the highest-``level`` ``build`` grant so P3/P4 can
        decide which Ting workflow the Valkyrie may commission and at what
        autonomy level. Returns ``None`` when the realm is unknown or has no
        ``build`` grant.
        """
        realm = await self._repo.get_realm(realm_slug_or_valkyrie_id)
        if realm is None:
            return None

        grants = await self._repo.list_trust_grants(realm.id)
        build_grants = [g for g in grants if g.action_class == BUILD_ACTION_CLASS]
        if not build_grants:
            return None

        return max(build_grants, key=lambda g: g.level)
