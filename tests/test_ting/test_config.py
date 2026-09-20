"""Tests for Ting configuration."""

from __future__ import annotations

import pytest

from ting.config import (
    DatabaseConfig,
    LoggingConfig,
    Settings,
    WorkflowExecutionConfig,
)


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

    def test_shared_integrations_accepts_platform_env_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INTEGRATIONS__DATABASE_NAME", "niuu_shared")

        settings = Settings()

        assert settings.shared_integrations.database_name == "niuu_shared"

    def test_server_and_platform_legacy_aliases(self, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestWorkflowExecutionConfig:
    """The generic pack and its code-delivery specialization gate independently."""

    def test_defaults_are_generic_only(self) -> None:
        config = WorkflowExecutionConfig()
        assert config.enabled is False
        assert config.delivery.enabled is False
        # Delivery-only settings moved off the top-level model entirely.
        assert not hasattr(config, "evidence_policy_id")
        assert not hasattr(config, "integration_policy_id")
        assert not hasattr(config, "review_producers")

    def test_delivery_enabled_requires_workflow_execution_enabled(self) -> None:
        with pytest.raises(ValueError, match="requires workflow_execution.enabled"):
            WorkflowExecutionConfig(enabled=False, delivery={"enabled": True})

    def test_delivery_enabled_with_workflow_execution_enabled_is_accepted(self) -> None:
        config = WorkflowExecutionConfig(enabled=True, delivery={"enabled": True})
        assert config.delivery.enabled is True

    def test_rejects_ting_delivery_wait_observer_while_delivery_is_disabled(self) -> None:
        with pytest.raises(ValueError, match="ting.delivery"):
            WorkflowExecutionConfig(
                enabled=True,
                delivery={"enabled": False},
                wait_observers=[
                    {
                        "condition_type": "forge.checks",
                        "adapter": "ting.delivery.wait_observers.ForgeChecksWaitObserver",
                    }
                ],
            )

    def test_accepts_ting_delivery_wait_observer_once_delivery_is_enabled(self) -> None:
        config = WorkflowExecutionConfig(
            enabled=True,
            delivery={"enabled": True},
            wait_observers=[
                {
                    "condition_type": "forge.checks",
                    "adapter": "ting.delivery.wait_observers.ForgeChecksWaitObserver",
                }
            ],
        )
        assert config.wait_observers[0]["adapter"].endswith("ForgeChecksWaitObserver")

    def test_generic_only_wait_observer_is_unaffected_by_delivery_state(self) -> None:
        config = WorkflowExecutionConfig(
            enabled=True,
            delivery={"enabled": False},
            wait_observers=[
                {
                    "condition_type": "timer",
                    "adapter": "ting.adapters.timer_wait_observer.TimerWaitObserver",
                }
            ],
        )
        assert config.wait_observers[0]["condition_type"] == "timer"
