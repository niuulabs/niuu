"""PostgreSQL adapter for the push device-token repository."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC
from uuid import UUID

import asyncpg

from volundr.domain.models import DevicePlatform, DeviceToken
from volundr.domain.ports import PushTokenRegistry
from volundr.domain.push import PushEnvironment, PushTokenKind

UPSERT_SQL = """INSERT INTO device_tokens
    (id, owner_id, platform, token, app_bundle_id, kind, environment, activity_id,
     created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (token, kind) DO UPDATE
    SET owner_id = EXCLUDED.owner_id,
        platform = EXCLUDED.platform,
        app_bundle_id = EXCLUDED.app_bundle_id,
        environment = EXCLUDED.environment,
        activity_id = EXCLUDED.activity_id,
        updated_at = EXCLUDED.updated_at
RETURNING *"""

# A running Live Activity has one update token: a new one replaces the earlier.
REPLACE_ACTIVITY_TOKEN_SQL = """DELETE FROM device_tokens
WHERE owner_id = $1 AND kind = $2 AND activity_id = $3 AND token <> $4"""

_INTERVAL_ELAPSED = (
    "(last_push_at IS NULL OR last_push_at <= now() - make_interval(secs => $2::double precision))"
)

CLAIM_PUSH_SQL = f"""UPDATE device_tokens
SET last_push_at = now(), push_due = FALSE
WHERE id = $1 AND {_INTERVAL_ELAPSED}
RETURNING id"""

MARK_PUSH_DUE_SQL = "UPDATE device_tokens SET push_due = TRUE WHERE id = $1"

RELEASE_PUSH_SQL = "UPDATE device_tokens SET last_push_at = NULL, push_due = TRUE WHERE id = $1"

CLAIM_OWED_PUSHES_SQL = f"""UPDATE device_tokens
SET last_push_at = now(), push_due = FALSE
WHERE kind = $1 AND push_due AND {_INTERVAL_ELAPSED}
RETURNING *"""


class PostgresDeviceTokenRepository(PushTokenRegistry):
    """Raw-SQL implementation of PushTokenRegistry (every token kind, with throttles)."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(self, device: DeviceToken) -> DeviceToken:
        """Register a token, refreshing (and re-owning) it if (token, kind) exists."""
        row = await self._pool.fetchrow(
            UPSERT_SQL,
            device.id,
            device.owner_id,
            device.platform.value,
            device.token,
            device.app_bundle_id,
            device.kind.value,
            # The table always names a host: an omitted environment is production.
            (device.environment or PushEnvironment.PRODUCTION).value,
            device.activity_id,
            device.created_at,
            device.updated_at,
        )
        if device.kind is PushTokenKind.LIVE_ACTIVITY and device.activity_id:
            await self._pool.execute(
                REPLACE_ACTIVITY_TOKEN_SQL,
                device.owner_id,
                device.kind.value,
                device.activity_id,
                device.token,
            )
        return self._row_to_device(row)

    async def list_for_owner(
        self, owner_id: str, *, kinds: Sequence[PushTokenKind] | None = None
    ) -> list[DeviceToken]:
        if kinds is None:
            rows = await self._pool.fetch(
                "SELECT * FROM device_tokens WHERE owner_id = $1 ORDER BY updated_at DESC",
                owner_id,
            )
            return [self._row_to_device(row) for row in rows]
        rows = await self._pool.fetch(
            """SELECT * FROM device_tokens
               WHERE owner_id = $1 AND kind = ANY($2::text[])
               ORDER BY updated_at DESC""",
            owner_id,
            [kind.value for kind in kinds],
        )
        return [self._row_to_device(row) for row in rows]

    async def delete(self, owner_id: str, token: str, *, kind: PushTokenKind | None = None) -> bool:
        if kind is None:
            result = await self._pool.execute(
                "DELETE FROM device_tokens WHERE owner_id = $1 AND token = $2",
                owner_id,
                token,
            )
            return result != "DELETE 0"
        result = await self._pool.execute(
            "DELETE FROM device_tokens WHERE owner_id = $1 AND token = $2 AND kind = $3",
            owner_id,
            token,
            kind.value,
        )
        return result != "DELETE 0"

    async def delete_registration(self, device_id: UUID) -> bool:
        result = await self._pool.execute("DELETE FROM device_tokens WHERE id = $1", device_id)
        return result != "DELETE 0"

    async def claim_push(self, device_id: UUID, *, min_interval_seconds: float) -> bool:
        claimed = await self._pool.fetchval(CLAIM_PUSH_SQL, device_id, float(min_interval_seconds))
        if claimed is not None:
            return True
        await self._pool.execute(MARK_PUSH_DUE_SQL, device_id)
        return False

    async def release_push(self, device_id: UUID) -> None:
        await self._pool.execute(RELEASE_PUSH_SQL, device_id)

    async def claim_owed_pushes(
        self, kind: PushTokenKind, *, min_interval_seconds: float
    ) -> list[DeviceToken]:
        rows = await self._pool.fetch(
            CLAIM_OWED_PUSHES_SQL, kind.value, float(min_interval_seconds)
        )
        return [self._row_to_device(row) for row in rows]

    async def count_by_environment(self) -> dict[str, dict[str, int]]:
        rows = await self._pool.fetch(
            "SELECT environment, kind, count(*) AS n FROM device_tokens GROUP BY 1, 2"
        )
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["environment"], {})[row["kind"]] = row["n"]
        return counts

    @staticmethod
    def _row_to_device(row: asyncpg.Record) -> DeviceToken:
        created_at = row["created_at"]
        updated_at = row["updated_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return DeviceToken(
            id=row["id"],
            owner_id=row["owner_id"],
            platform=DevicePlatform(row["platform"]),
            token=row["token"],
            app_bundle_id=row.get("app_bundle_id"),
            kind=PushTokenKind(row["kind"]),
            environment=PushEnvironment(row["environment"]),
            activity_id=row.get("activity_id"),
            last_push_at=row.get("last_push_at"),
            created_at=created_at,
            updated_at=updated_at,
        )
