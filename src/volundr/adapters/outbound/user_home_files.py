"""User-home file operations, also executed inside a volume-mounted helper pod.

Only the standard library is used so this worker can run in existing Skuld images.
Directory descriptors and no-follow opens keep concurrent symlink changes inside
this user's filesystem boundary. File contents never pass through pod logs.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import sys
from pathlib import PurePosixPath


def home_operation(base: str, operation: str, path: str) -> dict:
    if operation not in {"list", "delete"}:
        raise ValueError("Unsupported home operation")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or "\0" in path:
        raise ValueError("Path must stay inside your home")
    parts = parsed.parts
    if operation == "delete" and not parts:
        raise ValueError("Cannot delete your home directory")
    with contextlib.ExitStack() as stack:

        def open_dir(name, parent=None):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            return fd

        root = open_dir(base)
        fd = root
        for part in parts if operation == "list" else parts[:-1]:
            fd = open_dir(part, fd)
        if operation == "delete":
            leaf = parts[-1]
            info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                shutil.rmtree(leaf, dir_fd=fd)
            else:
                os.unlink(leaf, dir_fd=fd)
            return {"deleted": str(parsed)}
        entries = []
        for name in sorted(os.listdir(fd)):
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue  # A running process removed the entry while listing.
            kind = "directory" if stat.S_ISDIR(info.st_mode) else "file"
            if stat.S_ISLNK(info.st_mode):
                kind = "symlink"
            entries.append(
                {
                    "name": name,
                    "path": str(parsed / name),
                    "kind": kind,
                    "size": info.st_size,
                    "modified": info.st_mtime,
                }
            )
        capacity = os.fstatvfs(root)
        return {
            "path": "" if not parts else str(parsed),
            "entries": entries,
            "capacity_bytes": capacity.f_blocks * capacity.f_frsize,
            "available_bytes": capacity.f_bavail * capacity.f_frsize,
        }


if __name__ == "__main__":
    try:
        result = home_operation(*sys.argv[1:])
    except (OSError, ValueError) as exc:
        result = {"error": type(exc).__name__, "detail": str(exc)}
    print(json.dumps(result))
