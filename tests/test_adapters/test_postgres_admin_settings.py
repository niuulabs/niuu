"""Tests for the PostgreSQL admin settings repository."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.postgres_admin_settings import PostgresAdminSettingsRepository


def _row(section: str, data: object) -> dict:
    return {"section": section, "data": data}


class TestLoad:
    async def test_decodes_text_jsonb(self):
        pool = AsyncMock()
        pool.fetch.return_value = [_row("storage", json.dumps({"home_enabled": False}))]
        repo = PostgresAdminSettingsRepository(pool)

        assert await repo.load() == {"storage": {"home_enabled": False}}

    async def test_accepts_already_decoded_objects(self):
        pool = AsyncMock()
        pool.fetch.return_value = [_row("storage", {"file_manager_enabled": True})]
        repo = PostgresAdminSettingsRepository(pool)

        assert await repo.load() == {"storage": {"file_manager_enabled": True}}

    async def test_empty_table_is_no_settings(self):
        pool = AsyncMock()
        pool.fetch.return_value = []
        repo = PostgresAdminSettingsRepository(pool)

        assert await repo.load() == {}

    async def test_non_object_row_is_an_error(self):
        pool = AsyncMock()
        pool.fetch.return_value = [_row("storage", json.dumps([1, 2]))]
        repo = PostgresAdminSettingsRepository(pool)

        with pytest.raises(ValueError, match="expected an object"):
            await repo.load()


class TestSave:
    async def test_upserts_the_section_as_json(self):
        pool = AsyncMock()
        repo = PostgresAdminSettingsRepository(pool)

        await repo.save("storage", {"home_enabled": True, "file_manager_enabled": False})

        pool.execute.assert_awaited_once()
        sql, section, payload = pool.execute.call_args[0]
        assert "INSERT INTO admin_settings" in sql
        assert "ON CONFLICT (section)" in sql
        assert section == "storage"
        assert json.loads(payload) == {"home_enabled": True, "file_manager_enabled": False}
