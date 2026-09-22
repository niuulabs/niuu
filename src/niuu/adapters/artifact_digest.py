"""Filesystem-backed artifact digest resolution for evidence gates."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath

from niuu.ports.evidence import ArtifactDigestResolver


class FilesystemArtifactDigestResolver(ArtifactDigestResolver):
    """Hash a graph-pinned file confined to a deployment-configured root."""

    def __init__(self, *, root: str, chunk_size_bytes: int = 1024 * 1024) -> None:
        if not root.strip():
            raise ValueError("Artifact digest root is required")
        if type(chunk_size_bytes) is not int or chunk_size_bytes <= 0:
            raise ValueError("Artifact digest chunk size must be a positive integer")
        resolved_root = Path(root).expanduser().resolve(strict=True)
        if not resolved_root.is_dir():
            raise ValueError("Artifact digest root must be a directory")
        self._root = resolved_root
        self._chunk_size_bytes = chunk_size_bytes

    def digest(self, *, artifact_kind: str, artifact_id: str) -> str:
        if not artifact_kind.strip():
            raise ValueError("Artifact kind is required")
        path = PurePosixPath(artifact_id)
        if path.is_absolute() or artifact_id in {"", "."} or ".." in path.parts:
            raise ValueError("Artifact ID must be a relative path beneath the configured root")

        try:
            resolved = self._root.joinpath(*path.parts).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("Artifact does not exist beneath the configured root") from exc
        if not resolved.is_relative_to(self._root):
            raise ValueError("Artifact symlink escapes the configured root")
        if not resolved.is_file():
            raise ValueError("Artifact ID must resolve to a regular file")

        directory_fds: list[int] = []
        artifact_fd: int | None = None
        try:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory_fds.append(os.open(self._root, directory_flags))
            for part in path.parts[:-1]:
                directory_fds.append(os.open(part, directory_flags, dir_fd=directory_fds[-1]))
            artifact_fd = os.open(
                path.parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fds[-1],
            )
            before = os.fstat(artifact_fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Artifact ID must resolve to a regular file")
            if before.st_nlink < 1:
                raise ValueError("Artifact was deleted before it could be hashed")
            digest = hashlib.sha256()
            with os.fdopen(artifact_fd, "rb") as artifact:
                artifact_fd = None
                for chunk in iter(lambda: artifact.read(self._chunk_size_bytes), b""):
                    digest.update(chunk)
                after = os.fstat(artifact.fileno())
            if _stable_file_identity(before) != _stable_file_identity(after):
                raise ValueError("Artifact changed while it was being hashed")
        except OSError as exc:
            raise ValueError("Artifact could not be read beneath the configured root") from exc
        finally:
            if artifact_fd is not None:
                os.close(artifact_fd)
            for directory_fd in reversed(directory_fds):
                os.close(directory_fd)
        return digest.hexdigest()


def _stable_file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
