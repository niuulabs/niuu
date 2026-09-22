"""Constructor validation and fd-race edge cases for the filesystem digest resolver.

``tests/test_niuu/test_evidence.py`` already covers the resolver's happy path,
symlink-escape rejection, and deleted-artifact/invalid-chunk-size cases. This
file adds the remaining constructor validation and the fd-level races that can
only be exercised by intercepting the syscalls between the pathlib-level check
and the fd-confined read.
"""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from niuu.adapters.artifact_digest import FilesystemArtifactDigestResolver


def test_constructor_rejects_blank_root() -> None:
    with pytest.raises(ValueError, match="root is required"):
        FilesystemArtifactDigestResolver(root="   ")


def test_constructor_rejects_root_that_is_not_a_directory(tmp_path) -> None:
    not_a_dir = tmp_path / "not-a-dir.txt"
    not_a_dir.write_text("hello")
    with pytest.raises(ValueError, match="must be a directory"):
        FilesystemArtifactDigestResolver(root=str(not_a_dir))


def test_digest_rejects_blank_artifact_kind(tmp_path) -> None:
    resolver = FilesystemArtifactDigestResolver(root=str(tmp_path))
    with pytest.raises(ValueError, match="Artifact kind is required"):
        resolver.digest(artifact_kind="  ", artifact_id="whatever")


def test_digest_rejects_fd_opened_file_that_is_not_regular(tmp_path, monkeypatch) -> None:
    artifact = tmp_path / "doc.txt"
    artifact.write_text("hello")
    resolver = FilesystemArtifactDigestResolver(root=str(tmp_path))
    real_fstat = os.fstat
    calls = {"n": 0}

    def fake_fstat(fd):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(st_mode=0, st_nlink=1)
        return real_fstat(fd)

    monkeypatch.setattr("niuu.adapters.artifact_digest.os.fstat", fake_fstat)

    with pytest.raises(ValueError, match="must resolve to a regular file"):
        resolver.digest(artifact_kind="document", artifact_id="doc.txt")


def test_digest_rejects_artifact_with_no_remaining_links(tmp_path, monkeypatch) -> None:
    artifact = tmp_path / "doc.txt"
    artifact.write_text("hello")
    resolver = FilesystemArtifactDigestResolver(root=str(tmp_path))
    real_fstat = os.fstat
    calls = {"n": 0}

    def fake_fstat(fd):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=0)
        return real_fstat(fd)

    monkeypatch.setattr("niuu.adapters.artifact_digest.os.fstat", fake_fstat)

    with pytest.raises(ValueError, match="deleted before it could be hashed"):
        resolver.digest(artifact_kind="document", artifact_id="doc.txt")


def test_digest_rejects_artifact_that_changed_while_being_hashed(tmp_path, monkeypatch) -> None:
    artifact = tmp_path / "doc.txt"
    artifact.write_text("hello")
    resolver = FilesystemArtifactDigestResolver(root=str(tmp_path))
    real_fstat = os.fstat
    calls = {"n": 0}

    def fake_fstat(fd):
        calls["n"] += 1
        result = real_fstat(fd)
        if calls["n"] == 1:
            return result
        # Simulate a mtime change observed only on the post-read fstat.
        return SimpleNamespace(
            st_dev=result.st_dev,
            st_ino=result.st_ino,
            st_mode=result.st_mode,
            st_nlink=result.st_nlink,
            st_size=result.st_size,
            st_mtime_ns=result.st_mtime_ns + 1,
            st_ctime_ns=result.st_ctime_ns,
        )

    monkeypatch.setattr("niuu.adapters.artifact_digest.os.fstat", fake_fstat)

    with pytest.raises(ValueError, match="changed while it was being hashed"):
        resolver.digest(artifact_kind="document", artifact_id="doc.txt")


def test_digest_wraps_os_error_raised_while_opening_artifact(tmp_path, monkeypatch) -> None:
    artifact = tmp_path / "doc.txt"
    artifact.write_text("hello")
    resolver = FilesystemArtifactDigestResolver(root=str(tmp_path))
    real_open = os.open

    def fake_open(path, flags, *args, dir_fd=None, **kwargs):
        if path == "doc.txt":
            raise OSError("simulated permission error")
        return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr("niuu.adapters.artifact_digest.os.open", fake_open)

    with pytest.raises(ValueError, match="could not be read beneath the configured root"):
        resolver.digest(artifact_kind="document", artifact_id="doc.txt")
