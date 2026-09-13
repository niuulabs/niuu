"""PostgreSQL adapter for the admin settings edited on the Settings page."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from volundr.domain.ports import AdminSettingsRepository


class PostgresAdminSettingsRepository(AdminSettingsRepository):
    """One ``admin_settings`` row per section, the section's values as JSONB."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def load(self) -> dict[str, dict[str, Any]]:
        rows = await self._pool.fetch("SELECT section, data FROM admin_settings")
        return {row["section"]: _decode(row["data"]) for row in rows}

    async def save(self, section: str, values: dict[str, Any]) -> None:
        await self._pool.execute(
            """
            INSERT INTO admin_settings (section, data, updated_at)
            VALUES ($1, $2::jsonb, NOW())
            ON CONFLICT (section)
            DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
            """,
            section,
            json.dumps(values),
        )


def _decode(value: Any) -> dict[str, Any]:
    """asyncpg hands JSONB back as text unless a codec is registered."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f"admin_settings row holds {type(value).__name__}, expected an object")
    return value
