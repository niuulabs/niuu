"""Extended tests for cli.build — Nuitka build script and main command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cli.build import (
    DATA_DIR_MAPPINGS,
    DATA_FILE_MAPPINGS,
    build_cli,
    build_command,
    platform_suffix,
)

runner = CliRunner()


class TestPlatformSuffixNormalization:
    """Tests for architecture name normalization."""

    def test_x86_64_normalised_to_amd64(self) -> None:
        with patch("cli.build.platform.machine", return_value="x86_64"):
            result = platform_suffix()
        assert "amd64" in result

    def test_aarch64_normalised_to_arm64(self) -> None:
        with patch("cli.build.platform.machine", return_value="aarch64"):
            result = platform_suffix()
        assert "arm64" in result

    def test_arm64_stays_arm64(self) -> None:
        with patch("cli.build.platform.machine", return_value="arm64"):
            result = platform_suffix()
        assert "arm64" in result

    def test_unknown_arch_passed_through(self) -> None:
        with patch("cli.build.platform.machine", return_value="riscv64"):
            result = platform_suffix()
        assert "riscv64" in result

    def test_os_name_lowered(self) -> None:
        with patch("cli.build.platform.system", return_value="Linux"):
            result = platform_suffix()
        assert result.startswith("linux-")


class TestBuildCommandDataDirs:
    """Tests for data directory inclusion in build_command."""

    def test_web_dist_uses_current_frontend_build(self) -> None:
        assert DATA_DIR_MAPPINGS[0][0].as_posix().endswith("web-next/apps/niuu/dist")
        assert DATA_DIR_MAPPINGS[0][1] == "cli/web/dist"

    def test_migration_globs_use_source_of_truth(self) -> None:
        mappings = {src.as_posix(): dest for src, dest in DATA_FILE_MAPPINGS}

        assert mappings[next(src for src in mappings if src.endswith("migrations/*.sql"))] == (
            "cli/migrations/volundr/"
        )
        assert (
            mappings[next(src for src in mappings if src.endswith("migrations/ting/*.sql"))]
            == "cli/migrations/ting/"
        )

    def test_skips_empty_data_dirs(self) -> None:
        with patch.object(Path, "is_dir", return_value=False):
            cmd = build_command()
        # None of the data dir mappings should appear
        cmd_str = " ".join(cmd)
        for src_dir, dest in DATA_DIR_MAPPINGS:
            assert f"={dest}" not in cmd_str or "--include-data-dir" not in cmd_str

    def test_includes_populated_data_dirs(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "file.txt").write_text("content")

        with patch(
            "cli.build.DATA_DIR_MAPPINGS",
            [(data_dir, "cli/test/data")],
        ):
            cmd = build_command()

        cmd_str = " ".join(cmd)
        assert "--include-data-dir=" in cmd_str
        assert "cli/test/data" in cmd_str

    def test_includes_populated_data_file_globs(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "file.sql").write_text("select 1;")

        with patch(
            "cli.build.DATA_FILE_MAPPINGS",
            [(data_dir / "*.sql", "cli/test/data/")],
        ):
            cmd = build_command()

        cmd_str = " ".join(cmd)
        assert "--include-data-files=" in cmd_str
        assert "cli/test/data/" in cmd_str


class TestBuildCommandPginstall:
    """Tests for PostgreSQL binary inclusion via pginstall data dir."""

    def test_pginstall_in_data_dir_mappings(self) -> None:
        """The pginstall directory mapping should be present."""
        dest_names = [dest for _, dest in DATA_DIR_MAPPINGS]
        assert "niuu/pginstall" in dest_names

    def test_pginstall_included_when_dir_exists(self, tmp_path: Path) -> None:
        pginstall = tmp_path / "pginstall"
        pginstall.mkdir()
        (pginstall / "bin").mkdir()
        (pginstall / "bin" / "postgres").write_text("binary")

        with patch(
            "cli.build.DATA_DIR_MAPPINGS",
            [(pginstall, "niuu/pginstall")],
        ):
            cmd = build_command()

        cmd_str = " ".join(cmd)
        assert "niuu/pginstall" in cmd_str
        assert "--include-data-dir=" in cmd_str

    def test_no_pgserver_references_in_command(self) -> None:
        """Build command should not reference pgserver at all."""
        with patch("cli.build.DATA_DIR_MAPPINGS", []):
            cmd = build_command()
        cmd_str = " ".join(cmd)
        assert "pgserver" not in cmd_str


class TestBuildCliMain:
    """Tests for the build_cli main command (non-dry-run paths)."""

    def test_main_run_fails(self) -> None:
        with patch("cli.build.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = runner.invoke(build_cli, ["--name", "test-bin", "--output-dir", "/tmp/out"])
        assert result.exit_code == 1

    def test_main_run_succeeds(self, tmp_path: Path) -> None:
        output_dir = str(tmp_path / "dist")
        with patch("cli.build.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(build_cli, ["--name", "test-bin", "--output-dir", output_dir])
        assert result.exit_code == 0

    def test_dry_run_prints_command(self) -> None:
        result = runner.invoke(build_cli, ["--dry-run"])
        assert result.exit_code == 0
        assert "nuitka" in result.output
