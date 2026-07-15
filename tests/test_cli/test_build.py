"""Tests for the Nuitka build configuration."""

from __future__ import annotations

import tomllib

from typer.testing import CliRunner

from cli.build import (
    DEFAULT_BINARY_NAME,
    DEFAULT_ENTRY_POINT,
    INCLUDE_PACKAGE_DATA,
    INCLUDE_PACKAGES,
    NOFOLLOW_IMPORTS,
    REPO_ROOT,
    build_cli,
    build_command,
    platform_suffix,
)

runner = CliRunner()


class TestPlatformSuffix:
    """Tests for platform_suffix()."""

    def test_returns_string(self):
        result = platform_suffix()
        assert isinstance(result, str)
        assert "-" in result

    def test_contains_os_and_arch(self):
        result = platform_suffix()
        parts = result.split("-")
        assert len(parts) == 2
        assert parts[0] in ("linux", "darwin", "windows")
        assert parts[1] in ("amd64", "arm64", "x86_64", "aarch64") or parts[1]


class TestBuildCommand:
    """Tests for build_command()."""

    def test_starts_with_python_nuitka(self):
        cmd = build_command()
        assert cmd[1] == "-m"
        assert cmd[2] == "nuitka"

    def test_onefile_flag(self):
        cmd = build_command()
        assert "--onefile" in cmd

    def test_no_redundant_standalone_flag(self):
        cmd = build_command()
        assert "--standalone" not in cmd

    def test_follow_imports_flag(self):
        cmd = build_command()
        assert "--follow-imports" in cmd

    def test_includes_all_packages(self):
        cmd = build_command()
        cmd_str = " ".join(cmd)
        for pkg in INCLUDE_PACKAGES:
            assert f"--include-package={pkg}" in cmd_str

    def test_includes_package_data(self):
        cmd = build_command()
        cmd_str = " ".join(cmd)
        for pkg in INCLUDE_PACKAGE_DATA:
            assert f"--include-package-data={pkg}" in cmd_str

    def test_nofollows_test_imports(self):
        cmd = build_command()
        cmd_str = " ".join(cmd)
        nofollow = ",".join(NOFOLLOW_IMPORTS)
        assert f"--nofollow-import-to={nofollow}" in cmd_str

    def test_output_filename_contains_platform(self):
        cmd = build_command()
        suffix = platform_suffix()
        filename_flags = [c for c in cmd if c.startswith("--output-filename=")]
        assert len(filename_flags) == 1
        assert suffix in filename_flags[0]

    def test_custom_binary_name(self):
        cmd = build_command(binary_name="niuu-agent")
        filename_flags = [c for c in cmd if c.startswith("--output-filename=")]
        assert "niuu-agent" in filename_flags[0]

    def test_custom_output_dir(self):
        cmd = build_command(output_dir="/tmp/build")
        assert "--output-dir=/tmp/build" in cmd

    def test_entry_point_is_last_arg(self):
        cmd = build_command()
        assert cmd[-1] == DEFAULT_ENTRY_POINT


class TestMainDryRun:
    """Tests for the build script's main() in dry-run mode."""

    def test_dry_run_returns_zero(self):
        result = runner.invoke(build_cli, ["--dry-run"])
        assert result.exit_code == 0

    def test_dry_run_with_custom_name(self):
        result = runner.invoke(build_cli, ["--dry-run", "--name", "niuu-agent"])
        assert result.exit_code == 0


class TestConstants:
    """Tests for module-level constants."""

    def test_default_binary_name(self):
        assert DEFAULT_BINARY_NAME == "niuu"

    def test_default_entry_point_contains_cli(self):
        assert "cli" in DEFAULT_ENTRY_POINT
        assert "__main__" in DEFAULT_ENTRY_POINT

    def test_include_packages_has_all_five(self):
        required = {
            "audit",
            "bifrost",
            "cli",
            "credentials",
            "features",
            "guild",
            "identity",
            "integrations",
            "mimir",
            "niuu",
            "observatory",
            "personas",
            "ravn",
            "skuld",
            "tracker",
            "ting",
            "volundr",
        }
        assert required.issubset(set(INCLUDE_PACKAGES))

    def test_includes_all_plugin_entry_point_packages(self):
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        entry_points = pyproject["project"]["entry-points"]["niuu.plugins"]
        module_roots = {target.split(".", 1)[0] for target in entry_points.values()}

        assert module_roots.issubset(set(INCLUDE_PACKAGES))

    def test_nofollow_excludes_test_frameworks(self):
        assert "pytest" in NOFOLLOW_IMPORTS
        assert "_pytest" in NOFOLLOW_IMPORTS
