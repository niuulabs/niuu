"""Tests for the embedded resource helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cli.resources import migration_dir, ordered_migration_files, web_dist_dir


def _mock_traversable(mock_files):
    """Extract the deeply-chained mock traversable from patched files()."""
    return mock_files.return_value.__truediv__.return_value.__truediv__.return_value


class TestWebDistDir:
    """Tests for web_dist_dir()."""

    def test_prefers_web_next_app_dist_when_available(self, tmp_path: Path):
        app_dist = tmp_path / "web-next" / "apps" / "niuu" / "dist"
        app_dist.mkdir(parents=True)

        with patch(
            "cli.resources.Path.resolve", return_value=tmp_path / "src" / "cli" / "resources.py"
        ):
            assert web_dist_dir() == app_dist

    def test_falls_back_to_embedded_cli_dist(self, tmp_path: Path):
        embedded_dist = tmp_path / "src" / "cli" / "web" / "dist"
        embedded_dist.mkdir(parents=True)

        with patch(
            "cli.resources.Path.resolve", return_value=tmp_path / "src" / "cli" / "resources.py"
        ):
            assert web_dist_dir() == embedded_dist

    def test_raises_when_no_dist_found(self):
        with patch("cli.resources.importlib.resources.files") as mock_files:
            trav = _mock_traversable(mock_files)
            trav.__str__ = lambda self: "/nonexistent/path"

            with patch("cli.resources._resource_path") as mock_rp:
                mock_rp.return_value = Path("/nonexistent/path")
                with patch("cli.resources.Path") as mock_path_cls:
                    mock_resolve = mock_path_cls.return_value.resolve.return_value
                    mock_resolve.parents.__getitem__ = lambda self, i: Path("/nonexistent")
                    mock_path_cls.return_value.is_dir.return_value = False
                    with pytest.raises(FileNotFoundError, match="Web UI assets not found"):
                        web_dist_dir()


class TestMigrationDir:
    """Tests for migration_dir()."""

    def test_volundr_variant_default(self):
        """migration_dir returns a directory containing .up.sql files."""
        result = migration_dir("volundr")
        assert result.is_dir()
        assert any(result.glob("*.up.sql"))

    def test_volundr_migrations_include_session_spans(self):
        """The source migration stream includes trace span persistence."""
        result = migration_dir("volundr")
        migration_names = {path.name for path in ordered_migration_files(result)}

        assert "000042_session_spans.up.sql" in migration_names

    @pytest.mark.parametrize(
        ("source_relative", "embedded_relative"),
        [
            ("migrations", "src/cli/migrations/volundr"),
            ("migrations/ting", "src/cli/migrations/ting"),
        ],
    )
    def test_embedded_migrations_match_source(
        self,
        source_relative: str,
        embedded_relative: str,
    ) -> None:
        """Container and binary startup must not ship stale migration streams."""
        repo_root = Path(__file__).resolve().parents[2]
        source = repo_root / source_relative
        embedded = repo_root / embedded_relative

        source_files = {path.name: path.read_bytes() for path in source.glob("*.sql")}
        embedded_files = {path.name: path.read_bytes() for path in embedded.glob("*.sql")}

        assert embedded_files == source_files

    def test_ting_variant(self):
        """migration_dir('ting') returns a directory containing ting migrations."""
        result = migration_dir("ting")
        assert result.is_dir()
        assert any(result.glob("*.up.sql"))

    def test_raises_when_not_found(self):
        with patch("cli.resources.importlib.resources.files") as mock_files:
            trav = _mock_traversable(mock_files)
            trav.__str__ = lambda self: "/nonexistent"

            with patch(
                "cli.resources._resource_path",
                return_value=Path("/nonexistent"),
            ):
                with patch("cli.resources.Path") as mock_path_cls:
                    mock_resolve = mock_path_cls.return_value.resolve.return_value
                    mock_resolve.parents.__getitem__ = lambda self, i: Path("/nonexistent")
                    mock_path_cls.return_value.is_dir.return_value = False
                    with pytest.raises(FileNotFoundError, match="Migration files not found"):
                        migration_dir("volundr")


class TestOrderedMigrationFiles:
    def test_prioritizes_legacy_compatibility_first(self, tmp_path: Path) -> None:
        (tmp_path / "000001_initial_schema.up.sql").write_text("-- init")
        (tmp_path / "000025_legacy_schema_compat.up.sql").write_text("-- compat")
        (tmp_path / "000016_run_progress.up.sql").write_text("-- progress")

        ordered = [path.name for path in ordered_migration_files(tmp_path)]

        assert ordered == [
            "000025_legacy_schema_compat.up.sql",
            "000001_initial_schema.up.sql",
            "000016_run_progress.up.sql",
        ]
