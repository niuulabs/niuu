from __future__ import annotations

from pathlib import Path

import pytest

from volundr.adapters.outbound.local_process import _write_scoped_persona_source


def test_scoped_persona_write_rejects_alias_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Workflow persona alias"):
        _write_scoped_persona_source(tmp_path, "../../outside", "name: outside\n")
    assert not (tmp_path.parent / "outside.yaml").exists()


def test_scoped_persona_write_refuses_symlinked_runtime_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".ravn").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        _write_scoped_persona_source(tmp_path, "reviewer", "name: reviewer\n")
    assert not (outside / "personas" / "reviewer.yaml").exists()


def test_scoped_persona_write_creates_private_runtime_file(tmp_path: Path) -> None:
    path = _write_scoped_persona_source(tmp_path, "reviewer", "name: reviewer\n")

    assert path.read_text(encoding="utf-8") == "name: reviewer\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.parent.parent.stat().st_mode & 0o777 == 0o700


def test_scoped_persona_write_replaces_file_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text("sensitive\n", encoding="utf-8")
    persona_dir = tmp_path / ".ravn" / "personas"
    persona_dir.mkdir(parents=True)
    (persona_dir / "reviewer.yaml").symlink_to(outside)

    path = _write_scoped_persona_source(tmp_path, "reviewer", "name: reviewer\n")

    assert outside.read_text(encoding="utf-8") == "sensitive\n"
    assert path.is_symlink() is False
    assert path.read_text(encoding="utf-8") == "name: reviewer\n"
