"""PostgreSQL adapter for integration connection repository."""

from __future__ import annotations

import json
import logging

import asyncpg

from niuu.domain.models import IntegrationConnection, IntegrationType
from niuu.ports.integrations import IntegrationRepository

logger = logging.getLogger(__name__)


class PostgresIntegrationRepository(IntegrationRepository):
    """PostgreSQL implementation of IntegrationRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_connections(
        self,
        owner_id: str,
        integration_type: IntegrationType | None = None,
    ) -> list[IntegrationConnection]:
        """List connections for a user, optionally filtered by type."""
        conditions = ["owner_id = $1"]
        params: list = [owner_id]
        idx = 2

        if integration_type is not None:
            conditions.append(f"integration_type = ${idx}")
            params.append(str(integration_type))
            idx += 1

        where = " AND ".join(conditions)
        query = f"SELECT * FROM integration_connections WHERE {where} ORDER BY created_at DESC"
        rows = await self._pool.fetch(query, *params)
        return [self._row_to_connection(row) for row in rows]

    async def list_connections_global(
        self,
        integration_type: IntegrationType | None = None,
        *,
        slug: str | None = None,
        enabled_only: bool = False,
    ) -> list[IntegrationConnection]:
        """List connections across all owners with optional filters."""
        conditions: list[str] = []
        params: list = []
        idx = 1

        if integration_type is not None:
            conditions.append(f"integration_type = ${idx}")
            params.append(str(integration_type))
            idx += 1

        if slug is not None:
            conditions.append(f"slug = ${idx}")
            params.append(slug)
            idx += 1

        if enabled_only:
            conditions.append("enabled = true")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = (
            "SELECT * FROM integration_connections"
            f"{where} ORDER BY owner_id ASC, created_at DESC"
        )
        rows = await self._pool.fetch(query, *params)
        return [self._row_to_connection(row) for row in rows]

    async def get_connection(self, connection_id: str) -> IntegrationConnection | None:
        """Get a single connection by ID."""
        row = await self._pool.fetchrow(
            "SELECT * FROM integration_connections WHERE id = $1::uuid",
            connection_id,
        )
        if row is None:
            return None
        return self._row_to_connection(row)

    async def save_connection(
        self,
        connection: IntegrationConnection,
    ) -> IntegrationConnection:
        """Create or update a connection (upsert)."""
        await self._pool.execute(
            """
            INSERT INTO integration_connections
                (id, owner_id, integration_type, adapter, credential_name,
                 config, enabled, slug, created_at, updated_at)
            VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10)
            ON CONFLICT (id) DO UPDATE SET
                credential_name = EXCLUDED.credential_name,
                config = EXCLUDED.config,
                enabled = EXCLUDED.enabled,
                slug = EXCLUDED.slug,
                updated_at = EXCLUDED.updated_at
            """,
            connection.id,
            connection.owner_id,
            str(connection.integration_type),
            connection.adapter,
            connection.credential_name,
            json.dumps(connection.config),
            connection.enabled,
            connection.slug,
            connection.created_at,
            connection.updated_at,
        )
        return connection

    async def delete_connection(self, connection_id: str) -> None:
        """Delete a connection by ID."""
        await self._pool.execute(
            "DELETE FROM integration_connections WHERE id = $1::uuid",
            connection_id,
        )

    @staticmethod
    def _row_to_connection(row: asyncpg.Record) -> IntegrationConnection:
        """Convert a database row to an IntegrationConnection domain model."""
        config_raw = row["config"]
        if isinstance(config_raw, str):
            config = json.loads(config_raw)
        else:
            config = dict(config_raw) if config_raw else {}

        return IntegrationConnection(
            id=str(row["id"]),
            owner_id=row["owner_id"],
            integration_type=IntegrationType(row["integration_type"]),
            adapter=row["adapter"],
            credential_name=row["credential_name"],
            config=config,
            enabled=row["enabled"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            slug=row.get("slug", ""),
        )
