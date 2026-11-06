"""Atomic credential file writes."""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from volundr.guest_agent.errors import WriteError

FILE_MODE = 0o644
DIRECTORY_MODE = 0o755


@dataclass(frozen=True)
class PlannedFile:
    """One file to write; ``content`` is the credential, so it never appears in a repr."""

    directory: str
    name: str
    content: bytes

    def __repr__(self) -> str:
        return f"PlannedFile(directory={self.directory!r}, name={self.name!r})"

    @property
    def target(self) -> Path:
        return Path(self.directory) / self.name


def write_all(files: list[PlannedFile]) -> None:
    """Write every file; each one is complete or absent, never partial."""
    for planned in files:
        write_atomic(planned.target, planned.content, root=Path(planned.directory))


def write_atomic(target: Path, content: bytes, *, root: Path) -> None:
    """Write ``content`` to ``target`` via a temp file in the same directory.

    ``root`` is the configured directory; it and any directory created below it
    are set to 0755 (the process umask must not narrow what the guest's
    containers need to read).
    """
    try:
        _ensure_directory(target.parent, root)
        _replace(target, content)
    except OSError as exc:
        raise WriteError(f"cannot write {target}: {exc}") from exc


def _ensure_directory(directory: Path, root: Path) -> None:
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir()
        path.chmod(DIRECTORY_MODE)
    root.chmod(DIRECTORY_MODE)


def _replace(target: Path, content: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            os.fchmod(handle.fileno(), FILE_MODE)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise
    _fsync_directory(target.parent)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
