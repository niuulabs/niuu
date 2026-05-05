"""Tests for cli.commands.core and cli.commands.platform helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import click.exceptions
import pytest

from cli.commands.platform import (
    _build_preflight_config,
    _print_status,
    _resolve_enabled_services,
    _shutdown,
    _startup,
)
from cli.config import CLISettings, PerServiceConfig
from cli.services.manager import ServiceState, StartupError
from niuu.ports.plugin import ServiceDefinition


def _stub_service_def(
    name: str,
    default_enabled: bool = True,
    depends_on: list[str] | None = None,
) -> ServiceDefinition:
    return ServiceDefinition(
        name=name,
        description=f"{name} service",
        factory=MagicMock,
        default_enabled=default_enabled,
        depends_on=depends_on or [],
    )


class TestPrintStatus:
    def test_starting_prints_no_newline(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_status("volundr", ServiceState.STARTING)
        captured = capsys.readouterr()
        assert "Starting volundr" in captured.out

    def test_healthy_prints_ok(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_status("volundr", ServiceState.HEALTHY)
        captured = capsys.readouterr()
        assert "ok" in captured.out

    def test_unhealthy_prints_failed(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_status("volundr", ServiceState.UNHEALTHY)
        captured = capsys.readouterr()
        assert "FAILED" in captured.out

    def test_stopping_prints_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_status("tyr", ServiceState.STOPPING)
        captured = capsys.readouterr()
        assert "Stopping tyr" in captured.out

    def test_stopped_prints_done(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_status("tyr", ServiceState.STOPPED)
        captured = capsys.readouterr()
        assert "done" in captured.out


class TestBuildPreflightConfig:
    def test_builds_from_settings(self) -> None:
        settings = CLISettings()
        config = _build_preflight_config(settings)
        assert config.claude_binary == "claude"
        assert config.ports == [8080]
        assert config.workspaces_dir == "~/.niuu/workspaces"
        assert config.database_mode == "embedded"

    def test_collects_plugin_ports(self) -> None:
        settings = CLISettings(
            plugins={
                "enabled": {},
                "extra": [
                    {"adapter": "some.Plugin", "port": 8081},
                    {"adapter": "other.Plugin", "port": 9100},
                ],
            },
        )
        config = _build_preflight_config(settings)
        assert 8080 in config.ports
        assert 8081 in config.ports
        assert 9100 in config.ports

    def test_deduplicates_server_port_in_plugins(self) -> None:
        settings = CLISettings(
            plugins={
                "enabled": {},
                "extra": [
                    {"adapter": "some.Plugin", "port": 8080},
                ],
            },
        )
        config = _build_preflight_config(settings)
        assert config.ports == [8080]

    def test_ignores_non_int_ports(self) -> None:
        settings = CLISettings(
            plugins={
                "enabled": {},
                "extra": [
                    {"adapter": "some.Plugin", "port": "not-a-port"},
                    {"adapter": "other.Plugin"},
                ],
            },
        )
        config = _build_preflight_config(settings)
        assert config.ports == [8080]


class TestResolveEnabledServices:
    def test_uses_plugin_defaults(self) -> None:
        service_defs = {
            "volundr": _stub_service_def("volundr", default_enabled=True),
            "tyr": _stub_service_def("tyr", default_enabled=True),
            "skuld": _stub_service_def("skuld", default_enabled=False),
        }
        enabled = _resolve_enabled_services(service_defs, CLISettings(), False, {})
        assert "volundr" in enabled
        assert "tyr" in enabled
        assert "skuld" not in enabled

    def test_config_override_enables(self) -> None:
        service_defs = {
            "skuld": _stub_service_def("skuld", default_enabled=False),
        }
        settings = CLISettings(
            service_overrides={"skuld": PerServiceConfig(enabled=True)},
        )
        enabled = _resolve_enabled_services(service_defs, settings, False, {})
        assert "skuld" in enabled

    def test_config_override_disables(self) -> None:
        service_defs = {
            "volundr": _stub_service_def("volundr", default_enabled=True),
        }
        settings = CLISettings(
            service_overrides={"volundr": PerServiceConfig(enabled=False)},
        )
        enabled = _resolve_enabled_services(service_defs, settings, False, {})
        assert "volundr" not in enabled

    def test_cli_flag_true_overrides_config(self) -> None:
        service_defs = {
            "skuld": _stub_service_def("skuld", default_enabled=False),
        }
        settings = CLISettings(
            service_overrides={"skuld": PerServiceConfig(enabled=False)},
        )
        enabled = _resolve_enabled_services(service_defs, settings, False, {"skuld": True})
        assert "skuld" in enabled

    def test_cli_flag_false_overrides_config(self) -> None:
        service_defs = {
            "tyr": _stub_service_def("tyr", default_enabled=True),
        }
        enabled = _resolve_enabled_services(service_defs, CLISettings(), False, {"tyr": False})
        assert "tyr" not in enabled

    def test_cli_flag_none_does_not_override(self) -> None:
        service_defs = {
            "volundr": _stub_service_def("volundr", default_enabled=True),
        }
        enabled = _resolve_enabled_services(service_defs, CLISettings(), False, {"volundr": None})
        assert "volundr" in enabled

    def test_start_all_enables_everything(self) -> None:
        service_defs = {
            "volundr": _stub_service_def("volundr", default_enabled=True),
            "skuld": _stub_service_def("skuld", default_enabled=False),
        }
        enabled = _resolve_enabled_services(service_defs, CLISettings(), True, {})
        assert "volundr" in enabled
        assert "skuld" in enabled

    def test_start_all_ignores_no_flags(self) -> None:
        service_defs = {
            "tyr": _stub_service_def("tyr", default_enabled=True),
        }
        # --all overrides even --no-tyr
        enabled = _resolve_enabled_services(service_defs, CLISettings(), True, {"tyr": False})
        assert "tyr" in enabled


class TestStartup:
    async def test_startup_with_preflight_pass(self) -> None:
        manager = MagicMock()
        manager.start_all = AsyncMock()
        manager._registry = MagicMock()
        settings = CLISettings()

        passing_results = [MagicMock(passed=True, warn_only=False, name="test", message="ok")]
        mock_server = AsyncMock()
        mock_server.health_check = AsyncMock(return_value=True)
        with (
            patch(
                "cli.commands.platform.run_preflight_checks",
                return_value=passing_results,
            ),
            patch("cli.commands.platform.has_failures", return_value=False),
            patch("cli.commands.platform.format_results", return_value="all ok"),
            patch("niuu.app.RootServer", return_value=mock_server),
        ):
            await _startup(
                manager,
                settings,
                enabled_services=None,
                skip_preflight=False,
                host_profile="full",
                enabled_mounts=None,
            )
        manager.start_all.assert_awaited_once()

    async def test_startup_with_preflight_fail(self) -> None:
        manager = MagicMock()
        manager.start_all = AsyncMock()
        settings = CLISettings()

        failing_results = [MagicMock(passed=False, warn_only=False, name="test", message="bad")]
        with (
            patch(
                "cli.commands.platform.run_preflight_checks",
                return_value=failing_results,
            ),
            patch("cli.commands.platform.has_failures", return_value=True),
            patch("cli.commands.platform.format_results", return_value="FAIL"),
            pytest.raises(click.exceptions.Exit),
        ):
            await _startup(
                manager,
                settings,
                enabled_services=None,
                skip_preflight=False,
                host_profile="full",
                enabled_mounts=None,
            )
        manager.start_all.assert_not_awaited()

    async def test_startup_skip_preflight(self) -> None:
        manager = MagicMock()
        manager.start_all = AsyncMock()
        manager._registry = MagicMock()
        settings = CLISettings()

        mock_server = AsyncMock()
        mock_server.health_check = AsyncMock(return_value=True)
        with (
            patch("cli.commands.platform.run_preflight_checks") as mock_preflight,
            patch("niuu.app.RootServer", return_value=mock_server),
        ):
            await _startup(
                manager,
                settings,
                enabled_services=None,
                skip_preflight=True,
                host_profile="full",
                enabled_mounts=None,
            )
        mock_preflight.assert_not_called()
        manager.start_all.assert_awaited_once()

    async def test_startup_service_failure(self) -> None:
        manager = MagicMock()
        manager.start_all = AsyncMock(side_effect=StartupError("tyr", "health check failed"))
        settings = CLISettings()

        with pytest.raises(click.exceptions.Exit):
            await _startup(
                manager,
                settings,
                enabled_services=None,
                skip_preflight=True,
                host_profile="full",
                enabled_mounts=None,
            )

    async def test_startup_enabled_services_forwarded(self) -> None:
        manager = MagicMock()
        manager.start_all = AsyncMock()
        manager._registry = MagicMock()
        settings = CLISettings()
        enabled = {"volundr", "tyr"}
        mounts = {"niuu-api", "runtime-config"}

        mock_server = AsyncMock()
        mock_server.health_check = AsyncMock(return_value=True)
        with patch("niuu.app.RootServer", return_value=mock_server) as root_server_cls:
            await _startup(
                manager,
                settings,
                enabled_services=enabled,
                skip_preflight=True,
                host_profile="api",
                enabled_mounts=mounts,
            )
        manager.start_all.assert_awaited_once_with(
            enabled_services=enabled, rollback_on_failure=True
        )
        root_server_cls.assert_called_once_with(
            registry=manager._registry,
            host=settings.server.host,
            port=settings.server.port,
            host_profile="api",
            enabled_mounts=mounts,
        )


class TestShutdown:
    async def test_shutdown_calls_stop_all(self) -> None:
        manager = MagicMock()
        manager.stop_all = AsyncMock()
        manager._root_server = None  # No root server in test
        await _shutdown(manager)
        manager.stop_all.assert_awaited_once()
