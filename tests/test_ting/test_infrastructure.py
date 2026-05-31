"""Tests for Ting infrastructure layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ting.config import DatabaseConfig
from ting.infrastructure.database import create_pool, database_pool, init_db


class TestCreatePool:
    @pytest.mark.asyncio
    async def test_create_pool_calls_asyncpg(self) -> None:
        config = DatabaseConfig(
            host="db-host",
            port=5433,
            user="ting_user",
            password="secret",
            name="ting_db",
            min_pool_size=2,
            max_pool_size=10,
        )
        mock_pool = MagicMock()

        with patch("ting.infrastructure.database.asyncpg.create_pool", new_callable=AsyncMock) as m:
            m.return_value = mock_pool
            result = await create_pool(config)

        assert result is mock_pool
        m.assert_called_once_with(
            host="db-host",
            port=5433,
            user="ting_user",
            password="secret",
            database="ting_db",
            min_size=2,
            max_size=10,
        )


class TestInitDb:
    @pytest.mark.asyncio
    async def test_init_db_is_noop(self) -> None:
        mock_pool = MagicMock()
        # Should not raise
        await init_db(mock_pool)


class TestDatabasePool:
    @pytest.mark.asyncio
    async def test_lifecycle(self) -> None:
        config = DatabaseConfig()
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        with patch("ting.infrastructure.database.create_pool", new_callable=AsyncMock) as m_create:
            m_create.return_value = mock_pool
            async with database_pool(config) as pool:
                assert pool is mock_pool

        mock_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pool_closes_on_exception(self) -> None:
        config = DatabaseConfig()
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        with patch("ting.infrastructure.database.create_pool", new_callable=AsyncMock) as m_create:
            m_create.return_value = mock_pool
            caught = False
            try:
                async with database_pool(config):
                    raise RuntimeError("boom")
            except RuntimeError as exc:
                caught = True
                assert str(exc) == "boom"
            assert caught

            mock_pool.close.assert_awaited_once()
