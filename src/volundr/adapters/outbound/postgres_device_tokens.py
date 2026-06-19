"""PostgreSQL adapter for the push device-token repository."""

from __future__ import annotations

from datetime import UTC

import asyncpg

from volundr.domain.models import DevicePlatform, DeviceToken
from volundr.domain.ports import DeviceTokenRepository


class PostgresDeviceTokenRepository(DeviceTokenRepository):
    """Raw-SQL implementation of DeviceTokenRepository."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(self, device: DeviceToken) -> DeviceToken:
        """Register a device, refreshing it if (owner, token) already exists."""
        await self._pool.execute(
            """
            INSERT INTO device_tokens
                (id, owner_id, platform, token, app_bundle_id, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (owner_id, token) DO UPDATE
                SET platform = EXCLUDED.platform,
                    app_bundle_id = EXCLUDED.app_bundle_id,
                    updated_at = EXCLUDED.updated_at
            """,
            device.id,
            device.owner_id,
            device.platform.value,
            device.token,
            device.app_bundle_id,
            device.created_at,
            device.updated_at,
        )
        return device

    async def list_for_owner(self, owner_id: str) -> list[DeviceToken]:
        rows = await self._pool.fetch(
            "SELECT * FROM device_tokens WHERE owner_id = $1 ORDER BY updated_at DESC",
            owner_id,
        )
        return [self._row_to_device(row) for row in rows]

    async def delete(self, owner_id: str, token: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM device_tokens WHERE owner_id = $1 AND token = $2",
            owner_id,
            token,
        )
        return result == "DELETE 1"

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
            created_at=created_at,
            updated_at=updated_at,
        )
