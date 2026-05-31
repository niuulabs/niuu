"""Tests for database infrastructure module."""

from unittest.mock import AsyncMock, MagicMock, patch

from volundr.config import DatabaseConfig
from volundr.infrastructure.database import (
    CHRONICLE_EVENTS_INDEX_SQL,
    CHRONICLE_EVENTS_TABLE_SQL,
    CHRONICLES_INDEX_SQL,
    CHRONICLES_TABLE_SQL,
    CREATE_INDEX_SQL,
    SESSIONS_TABLE_SQL,
    TOKEN_USAGE_INDEX_SQL,
    TOKEN_USAGE_TABLE_SQL,
    create_pool,
    database_pool,
    init_db,
)


class TestSchemaDefinitions:
    """Tests for SQL schema definitions."""

    def test_sessions_table_sql_has_required_columns(self):
        """Test that sessions table SQL has required columns."""
        assert "id UUID PRIMARY KEY" in SESSIONS_TABLE_SQL
        assert "name VARCHAR(255)" in SESSIONS_TABLE_SQL
        assert "model VARCHAR(100)" in SESSIONS_TABLE_SQL
        assert "status VARCHAR(20)" in SESSIONS_TABLE_SQL
        assert "chat_endpoint TEXT" in SESSIONS_TABLE_SQL
        assert "code_endpoint TEXT" in SESSIONS_TABLE_SQL
        assert "created_at TIMESTAMP" in SESSIONS_TABLE_SQL
        assert "updated_at TIMESTAMP" in SESSIONS_TABLE_SQL

    def test_sessions_table_sql_is_idempotent(self):
        """Test that sessions table SQL uses IF NOT EXISTS."""
        assert "IF NOT EXISTS" in SESSIONS_TABLE_SQL

    def test_index_sql_creates_indexes(self):
        """Test that index SQL creates expected indexes."""
        assert "idx_sessions_status" in CREATE_INDEX_SQL
        assert "idx_sessions_created_at" in CREATE_INDEX_SQL
        assert "IF NOT EXISTS" in CREATE_INDEX_SQL

    def test_token_usage_table_sql_has_required_columns(self):
        """Test that token_usage table SQL has required columns."""
        assert "id UUID PRIMARY KEY" in TOKEN_USAGE_TABLE_SQL
        assert "session_id UUID NOT NULL" in TOKEN_USAGE_TABLE_SQL
        assert "recorded_at TIMESTAMP" in TOKEN_USAGE_TABLE_SQL
        assert "tokens INTEGER NOT NULL" in TOKEN_USAGE_TABLE_SQL
        assert "provider VARCHAR(20)" in TOKEN_USAGE_TABLE_SQL
        assert "model VARCHAR(100)" in TOKEN_USAGE_TABLE_SQL
        assert "cost NUMERIC" in TOKEN_USAGE_TABLE_SQL
        assert "REFERENCES sessions(id)" in TOKEN_USAGE_TABLE_SQL

    def test_token_usage_table_sql_is_idempotent(self):
        """Test that token_usage table SQL uses IF NOT EXISTS."""
        assert "IF NOT EXISTS" in TOKEN_USAGE_TABLE_SQL

    def test_token_usage_index_sql_creates_indexes(self):
        """Test that token_usage index SQL creates expected indexes."""
        assert "idx_token_usage_recorded_at" in TOKEN_USAGE_INDEX_SQL
        assert "idx_token_usage_session_id" in TOKEN_USAGE_INDEX_SQL
        assert "IF NOT EXISTS" in TOKEN_USAGE_INDEX_SQL

    def test_chronicles_table_sql_has_required_columns(self):
        """Test that chronicles table SQL has required columns."""
        assert "id UUID PRIMARY KEY" in CHRONICLES_TABLE_SQL
        assert "session_id UUID" in CHRONICLES_TABLE_SQL
        assert "status VARCHAR(20)" in CHRONICLES_TABLE_SQL
        assert "project VARCHAR(255)" in CHRONICLES_TABLE_SQL
        assert "config_snapshot JSONB" in CHRONICLES_TABLE_SQL
        assert "parent_chronicle_id UUID" in CHRONICLES_TABLE_SQL
        assert "IF NOT EXISTS" in CHRONICLES_TABLE_SQL

    def test_chronicles_index_sql_creates_indexes(self):
        """Test that chronicles index SQL creates expected indexes."""
        assert "idx_chronicles_session_id" in CHRONICLES_INDEX_SQL
        assert "idx_chronicles_project" in CHRONICLES_INDEX_SQL
        assert "idx_chronicles_tags" in CHRONICLES_INDEX_SQL
        assert "IF NOT EXISTS" in CHRONICLES_INDEX_SQL

    def test_chronicle_events_table_sql_has_required_columns(self):
        """Test that chronicle_events table SQL has required columns."""
        assert "id UUID PRIMARY KEY" in CHRONICLE_EVENTS_TABLE_SQL
        assert "chronicle_id UUID NOT NULL" in CHRONICLE_EVENTS_TABLE_SQL
        assert "session_id UUID NOT NULL" in CHRONICLE_EVENTS_TABLE_SQL
        assert "t INTEGER NOT NULL" in CHRONICLE_EVENTS_TABLE_SQL
        assert "type VARCHAR(20)" in CHRONICLE_EVENTS_TABLE_SQL
        assert "label TEXT NOT NULL" in CHRONICLE_EVENTS_TABLE_SQL
        assert "IF NOT EXISTS" in CHRONICLE_EVENTS_TABLE_SQL

    def test_chronicle_events_index_sql_creates_indexes(self):
        """Test that chronicle_events index SQL creates expected indexes."""
        assert "idx_chronicle_events_chronicle_id" in CHRONICLE_EVENTS_INDEX_SQL
        assert "idx_chronicle_events_session_id" in CHRONICLE_EVENTS_INDEX_SQL
        assert "idx_chronicle_events_t" in CHRONICLE_EVENTS_INDEX_SQL
        assert "IF NOT EXISTS" in CHRONICLE_EVENTS_INDEX_SQL


class TestCreatePool:
    """Tests for create_pool function."""

    @patch("volundr.infrastructure.database.asyncpg.create_pool", new_callable=AsyncMock)
    async def test_create_pool_calls_asyncpg(self, mock_create_pool):
        """Test that create_pool calls asyncpg.create_pool with correct args."""
        mock_pool = MagicMock()
        mock_create_pool.return_value = mock_pool

        config = DatabaseConfig(
            host="db.example.com",
            port=5433,
            user="testuser",
            password="testpass",
            name="testdb",
            min_pool_size=2,
            max_pool_size=10,
        )

        result = await create_pool(config)

        mock_create_pool.assert_called_once_with(
            host="db.example.com",
            port=5433,
            user="testuser",
            password="testpass",
            database="testdb",
            min_size=2,
            max_size=10,
        )
        assert result == mock_pool


class TestInitDb:
    """Tests for init_db function."""

    async def test_init_db_is_runtime_noop(self):
        """Runtime schema setup is disabled in favor of migrations."""
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None

        await init_db(mock_pool)

        mock_pool.acquire.assert_not_called()
        mock_conn.execute.assert_not_called()


class TestDatabasePool:
    """Tests for database_pool context manager."""

    @patch("volundr.infrastructure.database.init_db")
    @patch("volundr.infrastructure.database.create_pool")
    async def test_database_pool_creates_and_closes(self, mock_create_pool, mock_init_db):
        """Test that database_pool creates pool, initializes db, and closes."""
        mock_pool = AsyncMock()
        mock_create_pool.return_value = mock_pool

        config = DatabaseConfig()

        async with database_pool(config) as pool:
            assert pool == mock_pool

        mock_create_pool.assert_called_once_with(config)
        mock_init_db.assert_called_once_with(mock_pool)
        mock_pool.close.assert_called_once()

    @patch("volundr.infrastructure.database.init_db")
    @patch("volundr.infrastructure.database.create_pool")
    async def test_database_pool_closes_on_exception(self, mock_create_pool, mock_init_db):
        """Test that database_pool closes pool even on exception."""
        mock_pool = AsyncMock()
        mock_create_pool.return_value = mock_pool

        config = DatabaseConfig()

        caught = False
        try:
            async with database_pool(config):
                raise RuntimeError("Test error")
        except RuntimeError as exc:
            caught = True
            assert str(exc) == "Test error"
        assert caught

        mock_pool.close.assert_called_once()
