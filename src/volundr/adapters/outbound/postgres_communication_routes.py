"""PostgreSQL adapter for communication route persistence."""

from __future__ import annotations

import json
from datetime import UTC
from uuid import UUID

import asyncpg

from volundr.domain.models import CommunicationPlatform, CommunicationRoute, CommunicationRouteMode
from volundr.domain.ports import CommunicationRouteRepository


class PostgresCommunicationRouteRepository(CommunicationRouteRepository):
    """Store platform conversation/thread routes for active sessions."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_route(self, route: CommunicationRoute) -> CommunicationRoute:
        await self._pool.execute(
            """
            INSERT INTO communication_routes
                (id, platform, conversation_id, thread_id, session_id, owner_id,
                 mode, default_target, active, metadata, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12)
            ON CONFLICT (id) DO UPDATE SET
                platform = EXCLUDED.platform,
                conversation_id = EXCLUDED.conversation_id,
                thread_id = EXCLUDED.thread_id,
                session_id = EXCLUDED.session_id,
                owner_id = EXCLUDED.owner_id,
                mode = EXCLUDED.mode,
                default_target = EXCLUDED.default_target,
                active = EXCLUDED.active,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            """,
            route.id,
            route.platform.value,
            route.conversation_id,
            route.thread_id,
            route.session_id,
            route.owner_id,
            route.mode.value,
            route.default_target,
            route.active,
            json.dumps(route.metadata),
            route.created_at,
            route.updated_at,
        )
        return route

    async def get_active_route(
        self,
        platform: str,
        conversation_id: str,
        thread_id: str | None,
    ) -> CommunicationRoute | None:
        row = await self._pool.fetchrow(
            """
            SELECT *
            FROM communication_routes
            WHERE platform = $1
              AND conversation_id = $2
              AND thread_id IS NOT DISTINCT FROM $3
              AND active = true
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            platform,
            conversation_id,
            thread_id,
        )
        if row is None:
            return None
        return self._row_to_route(row)

    async def deactivate_routes_for_session(self, session_id: UUID) -> int:
        result = await self._pool.execute(
            """
            UPDATE communication_routes
               SET active = false,
                   updated_at = NOW()
             WHERE session_id = $1
               AND active = true
            """,
            session_id,
        )
        return int(result.split()[-1])

    async def list_routes_for_session(self, session_id: UUID) -> list[CommunicationRoute]:
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM communication_routes
            WHERE session_id = $1
            ORDER BY created_at ASC
            """,
            session_id,
        )
        return [self._row_to_route(row) for row in rows]

    @staticmethod
    def _row_to_route(row: asyncpg.Record) -> CommunicationRoute:
        metadata_raw = row["metadata"]
        if isinstance(metadata_raw, str):
            metadata = json.loads(metadata_raw)
        else:
            metadata = dict(metadata_raw) if metadata_raw else {}

        created_at = row["created_at"]
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        updated_at = row["updated_at"]
        if updated_at is not None and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)

        return CommunicationRoute(
            id=row["id"],
            platform=CommunicationPlatform(row["platform"]),
            conversation_id=row["conversation_id"],
            thread_id=row["thread_id"],
            session_id=row["session_id"],
            owner_id=row["owner_id"],
            mode=CommunicationRouteMode(row["mode"]),
            default_target=row["default_target"],
            active=row["active"],
            metadata=metadata,
            created_at=created_at,
            updated_at=updated_at,
        )
