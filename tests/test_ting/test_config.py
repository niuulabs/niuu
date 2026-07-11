"""Tests for Ting configuration."""

from __future__ import annotations

import pytest

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

    def test_server_and_platform_legacy_aliases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOST", "127.0.0.2")
        monkeypatch.setenv("PORT", "8181")
        monkeypatch.setenv("WORKERS", "2")
        monkeypatch.setenv("NIUU_SERVER_HOST", "192.0.2.10")
        monkeypatch.setenv("NIUU_SERVER_PORT", "9090")

        settings = Settings()

        assert settings.server_host == "127.0.0.2"
        assert settings.server_port == 8181
        assert settings.server_workers == 2
        assert settings.local_platform_host == "192.0.2.10"
        assert settings.local_platform_port == 9090

    @pytest.mark.parametrize(
        ("name", "value", "field"),
        [
            ("PORT", "invalid", "server_port"),
            ("PORT", "70000", "server_port"),
            ("WORKERS", "0", "server_workers"),
            ("NIUU_SERVER_PORT", "0", "local_platform_port"),
        ],
    )
    def test_malformed_server_aliases_fail_loudly(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
        field: str,
    ) -> None:
        monkeypatch.setenv(name, value)

        with pytest.raises(ValueError, match=field):
            Settings()
