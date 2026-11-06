from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from volundr.guest_agent.errors import WriteError
from volundr.guest_agent.writer import PlannedFile, write_all, write_atomic


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_writes_content_with_contract_modes(tmp_path: Path) -> None:
    directory = tmp_path / "run" / "tokens" / "identity"

    write_all([PlannedFile(directory=str(directory), name="token", content=b"jwt")])

    target = directory / "token"
    assert target.read_bytes() == b"jwt"
    assert mode(target) == 0o644
    assert mode(directory) == 0o755
    assert mode(directory.parent) == 0o755
    assert mode(directory.parent.parent) == 0o755


def test_modes_ignore_a_restrictive_umask(tmp_path: Path) -> None:
    previous = os.umask(0o077)
    try:
        directory = tmp_path / "a" / "b"
        write_all([PlannedFile(directory=str(directory), name="token", content=b"x")])
    finally:
        os.umask(previous)

    assert mode(directory / "token") == 0o644
    assert mode(directory) == 0o755
    assert mode(directory.parent) == 0o755


def test_nested_token_path_creates_its_parent(tmp_path: Path) -> None:
    write_all([PlannedFile(directory=str(tmp_path), name="sub/token", content=b"x")])

    assert (tmp_path / "sub" / "token").read_bytes() == b"x"
    assert mode(tmp_path / "sub") == 0o755


def test_replaces_an_existing_file_and_leaves_no_temp_file(tmp_path: Path) -> None:
    (tmp_path / "token").write_bytes(b"old")

    write_all([PlannedFile(directory=str(tmp_path), name="token", content=b"new")])

    assert (tmp_path / "token").read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["token"]


def test_failure_keeps_the_old_file_and_removes_the_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "token"
    target.mkdir()  # os.replace of a file onto a directory fails

    with pytest.raises(WriteError, match="cannot write"):
        write_atomic(target, b"new", root=tmp_path)

    assert target.is_dir()
    assert [p.name for p in tmp_path.iterdir()] == ["token"]


def test_readers_never_see_a_partial_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "token"
    target.write_bytes(b"old-complete")
    seen: list[bytes] = []
    real_replace = os.replace

    def spying_replace(source, destination):
        seen.append(target.read_bytes())  # before the rename the target is still the old file
        real_replace(source, destination)

    monkeypatch.setattr("volundr.guest_agent.writer.os.replace", spying_replace)

    write_atomic(target, b"new-complete", root=tmp_path)

    assert seen == [b"old-complete"]
    assert target.read_bytes() == b"new-complete"


def test_content_is_not_in_the_repr(tmp_path: Path) -> None:
    planned = PlannedFile(directory=str(tmp_path), name="token", content=b"super-secret")

    assert "super-secret" not in repr(planned)
