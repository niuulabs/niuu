"""Tests for cli.config — CLISettings."""

from __future__ import annotations

import pytest

from cli.config import (
    CLISettings,
    DatabaseConfig,
    PluginConfig,
    PodManagerConfig,
    ServerConfig,
    ServiceConfig,
    TUIConfig,
)


class TestCLISettings:
    def test_defaults(self) -> None:
        settings = CLISettings()
        assert settings.version == "0.1.0"
        assert settings.context == "local"
        assert settings.mode == "mini"
        assert isinstance(settings.plugins, PluginConfig)
        assert isinstance(settings.services, ServiceConfig)
        assert isinstance(settings.tui, TUIConfig)
        assert isinstance(settings.database, DatabaseConfig)
        assert isinstance(settings.pod_manager, PodManagerConfig)
        assert isinstance(settings.server, ServerConfig)

    def test_plugin_config_defaults(self) -> None:
        config = PluginConfig()
        assert config.enabled == {}
        assert config.extra == []

    def test_service_config_defaults(self) -> None:
        config = ServiceConfig()
        assert config.health_check_interval_seconds == 2.0
        assert config.health_check_timeout_seconds == 30.0
        assert config.health_check_max_retries == 15

    def test_tui_config_defaults(self) -> None:
        config = TUIConfig()
        assert config.theme == "textual-dark"

    def test_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIUU_CONTEXT", "remote")
        settings = CLISettings()
        assert settings.context == "remote"

    def test_docker_compute_requires_matching_vm_pod_manager(self) -> None:
        compute = {
            "pool_id": "acme",
            "max_machines": 1,
            "provider": {"adapter": "private.Provider"},
            "auth": {"adapter": "private.Auth"},
            "runtime": {"adapter": "private.Runtime"},
        }
        with pytest.raises(ValueError, match="VmPodManager"):
            CLISettings(mode="docker", compute=compute)

        configured = CLISettings(
            mode="docker",
            compute=compute,
            pod_manager={
                "adapter": "volundr.adapters.outbound.vm_pod_manager.VmPodManager",
                "profile": "cpu",
                "pool_id": "acme",
                "max_machines": 1,
            },
        )
        assert configured.compute is not None
        assert configured.compute.pool_id == "acme"


class TestDatabaseConfig:
    def test_defaults(self) -> None:
        config = DatabaseConfig()
        assert config.mode == "embedded"
        assert config.dsn == ""

    def test_external_mode(self) -> None:
        config = DatabaseConfig(mode="external", dsn="postgresql://localhost/niuu")
        assert config.mode == "external"
        assert config.dsn == "postgresql://localhost/niuu"


class TestPodManagerConfig:
    def test_defaults(self) -> None:
        config = PodManagerConfig()
        assert "LocalProcessPodManager" in config.adapter
        assert config.workspaces_dir == "~/.niuu/workspaces"
        assert config.claude_binary == "claude"
        assert config.max_concurrent == 4

    def test_custom_adapter(self) -> None:
        config = PodManagerConfig(adapter="custom.adapter.PodManager")
        assert config.adapter == "custom.adapter.PodManager"


class TestServerConfig:
    def test_defaults(self) -> None:
        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8080

    def test_custom_port(self) -> None:
        config = ServerConfig(port=9090)
        assert config.port == 9090
