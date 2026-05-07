"""Tests for the embedded resource helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cli.resources import migration_dir, web_dist_dir


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

    def test_tyr_variant(self):
        """migration_dir('tyr') returns a directory containing tyr migrations."""
        result = migration_dir("tyr")
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
