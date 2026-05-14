"""Tests for Ting configuration."""

from ting.config import DatabaseConfig, LoggingConfig, Settings


class TestDatabaseConfig:
    def test_defaults(self) -> None:
        config = DatabaseConfig()
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.user == "ting"
        assert config.password == "ting"
        assert config.name == "ting"
        assert config.min_pool_size == 5
        assert config.max_pool_size == 20

    def test_dsn(self) -> None:
        config = DatabaseConfig(host="db", port=5433, user="u", password="p", name="mydb")
        assert config.dsn == "postgresql://u:p@db:5433/mydb"

    def test_custom_values(self) -> None:
        config = DatabaseConfig(host="prod-db", min_pool_size=10, max_pool_size=50)
        assert config.host == "prod-db"
        assert config.min_pool_size == 10


class TestLoggingConfig:
    def test_defaults(self) -> None:
        config = LoggingConfig()
        assert config.level == "info"
        assert config.format == "text"


class TestSettings:
    def test_defaults(self) -> None:
        settings = Settings()
        assert isinstance(settings.database, DatabaseConfig)
        assert isinstance(settings.logging, LoggingConfig)

    def test_nested_override(self) -> None:
        settings = Settings(database=DatabaseConfig(host="custom-host"))
        assert settings.database.host == "custom-host"
