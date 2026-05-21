"""Tests for cli.app — Typer app factory and CLI command tree."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from cli.app import build_app
from cli.config import CLISettings
from cli.registry import PluginRegistry
from tests.test_cli.conftest import FakePlugin

runner = CliRunner()


def _build_test_app(
    plugins: list[FakePlugin] | None = None,
) -> tuple:
    """Build a test app with given plugins (no entry point discovery)."""
    settings = CLISettings()
    registry = PluginRegistry()
    for p in plugins or []:
        registry.register(p)
    app = build_app(settings=settings, registry=registry)
    return app, settings, registry


class TestVersionCommand:
    def test_version_flag(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "niuu 0.1.0" in result.output

    def test_version_short_flag(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert "niuu" in result.output

    def test_version_command(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestPlatformCommands:
    def test_platform_status_no_services(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["platform", "status"])
        assert result.exit_code == 0

    def test_platform_down(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["platform", "down"])
        assert result.exit_code == 0
        assert "stopped" in result.output.lower()

    def test_platform_init(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        app, _, _ = _build_test_app()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch("cli.commands.platform.typer.prompt", return_value="1"),
                patch("cli.commands.platform.Path.home", return_value=Path(tmp_dir)),
            ):
                result = runner.invoke(app, ["platform", "init"])
        assert result.exit_code == 0
        assert "setup complete" in result.output.lower()

    def test_platform_up_help(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["platform", "up", "--help"])
        assert result.exit_code == 0
        assert "skip-preflight" in result.output


class TestConfigCommand:
    def test_config_show_context(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "Context: local" in result.output

    def test_config_show_plugins(self) -> None:
        plugins = [FakePlugin(name="alpha"), FakePlugin(name="beta")]
        app, _, _ = _build_test_app(plugins=plugins)
        result = runner.invoke(app, ["config", "show"])
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_config_set(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["config", "set", "server.port", "9090"])
        assert result.exit_code == 0


class TestContextCommand:
    def test_context_list(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["context", "list"])
        assert result.exit_code == 0
        assert "local" in result.output

    def test_context_use(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["context", "use", "prod"])
        assert result.exit_code == 0
        assert "prod" in result.output

    def test_context_add(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["context", "add", "staging", "https://staging.niuu.io"])
        assert result.exit_code == 0

    def test_context_delete(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["context", "delete", "staging"])
        assert result.exit_code == 0


class TestIdentityCommands:
    def test_login_requires_issuer(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["login"])
        assert result.exit_code == 1
        assert "issuer" in result.output.lower()

    def test_logout(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["logout"])
        assert result.exit_code == 0
        assert "logged out" in result.output.lower()

    def test_whoami_not_authenticated(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 1
        assert "not authenticated" in result.output.lower()


class TestPluginWorkflowCommands:
    def test_plugin_commands_registered_at_top_level(self) -> None:
        """Plugin commands are mounted at top level, not under plugin name."""

        class _SessionPlugin(FakePlugin):
            def register_commands(self, app: typer.Typer) -> None:
                sessions = typer.Typer(no_args_is_help=True)

                @sessions.command()
                def list() -> None:
                    typer.echo("sessions list")

                app.add_typer(sessions, name="sessions")

        plugins = [_SessionPlugin(name="test")]
        app, _, _ = _build_test_app(plugins=plugins)
        result = runner.invoke(app, ["sessions", "list"])
        assert result.exit_code == 0
        assert "sessions list" in result.output

    def test_disabled_plugin_commands_not_registered(self) -> None:
        class _SessionPlugin(FakePlugin):
            def register_commands(self, app: typer.Typer) -> None:
                sessions = typer.Typer(no_args_is_help=True)

                @sessions.command()
                def list() -> None:
                    typer.echo("sessions list")

                app.add_typer(sessions, name="sessions")

        registry = PluginRegistry()
        registry.register(_SessionPlugin(name="disabled"))
        registry.disable("disabled")
        settings = CLISettings()
        app = build_app(settings=settings, registry=registry)
        result = runner.invoke(app, ["sessions", "list"])
        # sessions group was not registered because plugin is disabled
        assert result.exit_code != 0

    def test_plugin_without_commands_registers_nothing_extra(self) -> None:
        plugins = [FakePlugin(name="nocmds", has_commands=False)]
        app, _, _ = _build_test_app(plugins=plugins)
        # nocmds is not a registered sub-command
        result = runner.invoke(app, ["nocmds"])
        assert result.exit_code != 0


class TestHelpOutput:
    def test_help_shows_platform(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "platform" in result.output

    def test_help_shows_version(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["--help"])
        assert "version" in result.output

    def test_help_shows_identity_commands(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["--help"])
        assert "login" in result.output
        assert "logout" in result.output
        assert "whoami" in result.output

    def test_help_shows_config_and_context(self) -> None:
        app, _, _ = _build_test_app()
        result = runner.invoke(app, ["--help"])
        assert "config" in result.output
        assert "context" in result.output

    def test_no_volundr_or_ting_namespaces_at_top_level(self) -> None:
        """The old niuu volundr / niuu ting namespaces must be gone."""
        app, _, _ = _build_test_app()
        result_v = runner.invoke(app, ["volundr"])
        result_t = runner.invoke(app, ["ting"])
        assert result_v.exit_code != 0
        assert result_t.exit_code != 0

    def test_no_up_down_at_top_level(self) -> None:
        """up/down are now under platform, not at top level."""
        app, _, _ = _build_test_app()
        result_up = runner.invoke(app, ["up"])
        result_down = runner.invoke(app, ["down"])
        assert result_up.exit_code != 0
        assert result_down.exit_code != 0

    def test_no_migrate_or_serve_at_top_level(self) -> None:
        """migrate and serve have been removed."""
        app, _, _ = _build_test_app()
        assert runner.invoke(app, ["migrate"]).exit_code != 0
        assert runner.invoke(app, ["serve"]).exit_code != 0
