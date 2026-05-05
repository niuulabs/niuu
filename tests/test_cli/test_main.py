"""Tests for the Niuu CLI entry point (Typer-based)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cli.app import build_app
from cli.config import CLISettings
from cli.registry import PluginRegistry

runner = CliRunner()


def _app() -> build_app:
    """Build a test app with no plugin discovery."""
    return build_app(settings=CLISettings(), registry=PluginRegistry())


class TestCLIEntryPoint:
    """Tests for the Typer app produced by build_app."""

    def test_version_flag(self) -> None:
        result = runner.invoke(_app(), ["--version"])
        assert result.exit_code == 0
        assert "niuu" in result.output

    def test_all_expected_commands_registered(self) -> None:
        result = runner.invoke(_app(), ["--help"])
        assert result.exit_code == 0
        for cmd in ("platform", "config", "context", "version", "login", "logout", "whoami", "tui"):
            assert cmd in result.output, f"expected {cmd!r} in help output"

    def test_old_commands_gone(self) -> None:
        """Commands removed in NIU-405 must no longer exist at top level."""
        result = runner.invoke(_app(), ["--help"])
        for cmd in ("up", "down", "status", "migrate", "serve"):
            assert cmd not in result.output.split(), f"unexpected {cmd!r} at top level"

    def test_platform_up_command_exists(self) -> None:
        result = runner.invoke(_app(), ["platform", "up", "--help"])
        assert result.exit_code == 0

    def test_platform_down_command(self) -> None:
        result = runner.invoke(_app(), ["platform", "down"])
        assert result.exit_code == 0

    def test_platform_status_command(self) -> None:
        result = runner.invoke(_app(), ["platform", "status"])
        assert result.exit_code == 0

    def test_platform_init_command(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch("cli.commands.platform.typer.prompt", return_value="1"),
                patch("cli.commands.platform.Path.home", return_value=Path(tmp_dir)),
            ):
                result = runner.invoke(_app(), ["platform", "init"])
        assert result.exit_code == 0

    def test_version_command(self) -> None:
        result = runner.invoke(_app(), ["version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_config_show_command(self) -> None:
        result = runner.invoke(_app(), ["config", "show"])
        assert result.exit_code == 0

    def test_login_command_requires_issuer(self) -> None:
        result = runner.invoke(_app(), ["login"])
        assert result.exit_code == 1
        assert "issuer" in result.output.lower()

    def test_unknown_command_fails(self) -> None:
        result = runner.invoke(_app(), ["nonexistent"])
        assert result.exit_code != 0


class TestNiuuMainModule:
    def test_main_sets_default_config_when_exists(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".niuu" / "config.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("mode: mini\n")

        captured_env: dict[str, str] = {}

        def capture_app() -> None:
            captured_env["NIUU_CONFIG"] = os.environ.get("NIUU_CONFIG", "")

        mock_app = MagicMock(side_effect=capture_app)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("niuu.__main__.Path.home", return_value=tmp_path),
            patch("cli.app.build_app", return_value=mock_app),
        ):
            from niuu.__main__ import main

            main()

        assert captured_env["NIUU_CONFIG"] == str(config_file)

    def test_main_skips_default_when_config_does_not_exist(self, tmp_path: Path) -> None:
        captured_env: dict[str, str | None] = {}

        def capture_app() -> None:
            captured_env["NIUU_CONFIG"] = os.environ.get("NIUU_CONFIG")

        mock_app = MagicMock(side_effect=capture_app)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("niuu.__main__.Path.home", return_value=tmp_path),
            patch("cli.app.build_app", return_value=mock_app),
        ):
            from niuu.__main__ import main

            main()

        assert captured_env["NIUU_CONFIG"] is None
