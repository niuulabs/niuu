"""The per-start runtime dir and loopback secret."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from skuld.session_runtime import (
    TOKEN_FILE_NAME,
    RuntimeDirError,
    SessionRuntime,
    default_runtime_dir,
    prepare_runtime_dir,
    write_private_file,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_create_writes_a_private_token(tmp_path: Path) -> None:
    runtime = SessionRuntime.create(str(tmp_path / "rt"), "s-1")
    assert runtime.token_file == tmp_path / "rt" / TOKEN_FILE_NAME
    assert _mode(tmp_path / "rt") == 0o700
    assert _mode(runtime.token_file) == 0o600
    secret = runtime.token_file.read_text()
    assert runtime.authorization == f"Bearer {secret}"
    assert runtime.accepts(f"Bearer {secret}")
    assert runtime.accepts(f"Bearer {secret} ")


def test_secret_is_regenerated_per_start(tmp_path: Path) -> None:
    first = SessionRuntime.create(str(tmp_path / "rt"), "s-1")
    second = SessionRuntime.create(str(tmp_path / "rt"), "s-1")
    assert first.authorization != second.authorization
    assert not first.accepts(second.authorization)
    assert second.token_file.read_text() in second.authorization


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer ", "Basic x", "bearer abc"])
def test_rejects_missing_or_malformed_headers(tmp_path: Path, header: str | None) -> None:
    runtime = SessionRuntime.create(str(tmp_path / "rt"), "s-1")
    assert not runtime.accepts(header)


def test_revoke_only_removes_its_own_secret(tmp_path: Path) -> None:
    old = SessionRuntime.create(str(tmp_path / "rt"), "s-1")
    new = SessionRuntime.create(str(tmp_path / "rt"), "s-1")
    old.revoke()  # a replacement broker already owns the file
    assert new.token_file.exists()
    new.revoke()
    assert not new.token_file.exists()
    new.revoke()  # idempotent


def test_default_dir_is_per_session_and_private(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    root = prepare_runtime_dir("", "a/b c")
    assert root == default_runtime_dir("a/b c")
    assert root.name == "a_b_c"
    assert root.parent.name == f"skuld-{os.getuid()}"
    assert _mode(root) == 0o700 and _mode(root.parent) == 0o700


def test_existing_dir_permissions_are_tightened(tmp_path: Path) -> None:
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    loose.chmod(0o755)
    prepare_runtime_dir(str(loose), "s")
    assert _mode(loose) == 0o700


def test_refuses_a_symlinked_runtime_dir(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(RuntimeDirError, match="real directory"):
        prepare_runtime_dir(str(link), "s")


def test_refuses_a_foreign_owned_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(os, "getuid", lambda: os.stat(tmp_path).st_uid + 1)
    with pytest.raises(RuntimeDirError, match="owned by uid"):
        prepare_runtime_dir(str(tmp_path / "rt"), "s")


def test_path_rejects_traversal(tmp_path: Path) -> None:
    runtime = SessionRuntime.create(str(tmp_path / "rt"), "s")
    assert runtime.path("claude-mcp.json") == tmp_path / "rt" / "claude-mcp.json"
    for bad in ("", ".", "..", "a/b"):
        with pytest.raises(ValueError):
            runtime.path(bad)


def test_write_private_file_is_atomic_and_0600(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("old")
    target.chmod(0o644)
    write_private_file(target, "new")
    assert target.read_text() == "new"
    assert _mode(target) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]
