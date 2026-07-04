"""PostgreSQL adapter for the realm governance repository."""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from niuu.domain.models import Capability, Realm, TrustGrant
from niuu.ports.realm_repository import RealmRepository


class PostgresRealmRepository(RealmRepository):
    """PostgreSQL implementation of RealmRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Realms
    # ------------------------------------------------------------------

    async def list_realms(self) -> list[Realm]:
        """List all realms."""
        rows = await self._pool.fetch(
            """
            SELECT id, slug, name, sleipnir_domain, owner_id, instance_id,
                   autonomy_profile, created_at, updated_at
            FROM realms
            ORDER BY created_at DESC
            """
        )
        return [self._row_to_realm(row) for row in rows]

    async def get_realm(self, realm_ref: UUID | str) -> Realm | None:
        """Retrieve a realm by id (UUID) or slug (str)."""
        if isinstance(realm_ref, UUID):
            row = await self._pool.fetchrow(
                """
                SELECT id, slug, name, sleipnir_domain, owner_id, instance_id,
                       autonomy_profile, created_at, updated_at
                FROM realms
                WHERE id = $1
                """,
                realm_ref,
            )
            if row is None:
                return None
            return self._row_to_realm(row)

        row = await self._pool.fetchrow(
            """
            SELECT id, slug, name, sleipnir_domain, owner_id, instance_id,
                   autonomy_profile, created_at, updated_at
            FROM realms
            WHERE slug = $1
            """,
            realm_ref,
        )
        if row is None:
            return None
        return self._row_to_realm(row)

    async def save_realm(self, realm: Realm) -> Realm:
        """Create or update a realm (upsert by id)."""
        await self._pool.execute(
            """
            INSERT INTO realms
                (id, slug, name, sleipnir_domain, owner_id, instance_id,
                 autonomy_profile, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (id) DO UPDATE SET
                slug = EXCLUDED.slug,
                name = EXCLUDED.name,
                sleipnir_domain = EXCLUDED.sleipnir_domain,
                owner_id = EXCLUDED.owner_id,
                instance_id = EXCLUDED.instance_id,
                autonomy_profile = EXCLUDED.autonomy_profile,
                updated_at = EXCLUDED.updated_at
            """,
            realm.id,
            realm.slug,
            realm.name,
            realm.sleipnir_domain,
            realm.owner_id,
            realm.instance_id,
            realm.autonomy_profile,
            realm.created_at,
            realm.updated_at,
        )
        return realm

    # ------------------------------------------------------------------
    # Trust grants
    # ------------------------------------------------------------------

    async def list_trust_grants(self, realm_id: UUID) -> list[TrustGrant]:
        """List all trust grants for a realm."""
        rows = await self._pool.fetch(
            """
            SELECT id, realm_id, action_class, target, level, limits,
                   granted_by, granted_at
            FROM trust_grants
            WHERE realm_id = $1
            ORDER BY granted_at DESC
            """,
            realm_id,
        )
        return [self._row_to_grant(row) for row in rows]

    async def save_trust_grant(self, grant: TrustGrant) -> TrustGrant:
        """Create or update a trust grant (upsert by id)."""
        await self._pool.execute(
            """
            INSERT INTO trust_grants
                (id, realm_id, action_class, target, level, limits,
                 granted_by, granted_at)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                realm_id = EXCLUDED.realm_id,
                action_class = EXCLUDED.action_class,
                target = EXCLUDED.target,
                level = EXCLUDED.level,
                limits = EXCLUDED.limits,
                granted_by = EXCLUDED.granted_by,
                granted_at = EXCLUDED.granted_at
            """,
            grant.id,
            grant.realm_id,
            grant.action_class,
            grant.target,
            grant.level,
            json.dumps(grant.limits),
            grant.granted_by,
            grant.granted_at,
        )
        return grant

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    async def list_capabilities(self, realm_id: UUID) -> list[Capability]:
        """List all capabilities for a realm."""
        rows = await self._pool.fetch(
            """
            SELECT id, realm_id, name, kind, status, trust_level,
                   mimir_page_path, notes, created_at, updated_at
            FROM capabilities
            WHERE realm_id = $1
            ORDER BY created_at DESC
            """,
            realm_id,
        )
        return [self._row_to_capability(row) for row in rows]

    async def save_capability(self, capability: Capability) -> Capability:
        """Create or update a capability (upsert by id)."""
        await self._pool.execute(
            """
            INSERT INTO capabilities
                (id, realm_id, name, kind, status, trust_level,
                 mimir_page_path, notes, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (id) DO UPDATE SET
                realm_id = EXCLUDED.realm_id,
                name = EXCLUDED.name,
                kind = EXCLUDED.kind,
                status = EXCLUDED.status,
                trust_level = EXCLUDED.trust_level,
                mimir_page_path = EXCLUDED.mimir_page_path,
                notes = EXCLUDED.notes,
                updated_at = EXCLUDED.updated_at
            """,
            capability.id,
            capability.realm_id,
            capability.name,
            capability.kind,
            capability.status,
            capability.trust_level,
            capability.mimir_page_path,
            capability.notes,
            capability.created_at,
            capability.updated_at,
        )
        return capability

    async def upsert_capability(self, capability: Capability) -> Capability:
        """Insert or update a capability keyed on (realm_id, name)."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO capabilities
                (id, realm_id, name, kind, status, trust_level,
                 mimir_page_path, notes, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (realm_id, name) DO UPDATE SET
                kind = EXCLUDED.kind,
                status = EXCLUDED.status,
                trust_level = EXCLUDED.trust_level,
                mimir_page_path = EXCLUDED.mimir_page_path,
                notes = EXCLUDED.notes,
                updated_at = EXCLUDED.updated_at
            RETURNING id, realm_id, name, kind, status, trust_level,
                      mimir_page_path, notes, created_at, updated_at
            """,
            capability.id,
            capability.realm_id,
            capability.name,
            capability.kind,
            capability.status,
            capability.trust_level,
            capability.mimir_page_path,
            capability.notes,
            capability.created_at,
            capability.updated_at,
        )
        return self._row_to_capability(row)

    # ------------------------------------------------------------------
    # Row mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_realm(row: asyncpg.Record) -> Realm:
        """Convert a database row to a Realm domain model."""
        return Realm(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            sleipnir_domain=row["sleipnir_domain"],
            owner_id=row["owner_id"],
            instance_id=row["instance_id"],
            autonomy_profile=row["autonomy_profile"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_grant(row: asyncpg.Record) -> TrustGrant:
        """Convert a database row to a TrustGrant domain model."""
        limits_raw = row["limits"]
        if isinstance(limits_raw, str):
            limits = json.loads(limits_raw)
        else:
            limits = dict(limits_raw) if limits_raw else {}
        return TrustGrant(
            id=row["id"],
            realm_id=row["realm_id"],
            action_class=row["action_class"],
            target=row["target"],
            level=row["level"],
            limits=limits,
            granted_by=row["granted_by"],
            granted_at=row["granted_at"],
        )

    @staticmethod
    def _row_to_capability(row: asyncpg.Record) -> Capability:
        """Convert a database row to a Capability domain model."""
        return Capability(
            id=row["id"],
            realm_id=row["realm_id"],
            name=row["name"],
            kind=row["kind"],
            status=row["status"],
            trust_level=row["trust_level"],
            mimir_page_path=row["mimir_page_path"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
