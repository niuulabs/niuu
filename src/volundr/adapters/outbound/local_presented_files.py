"""Filesystem store for durable presented files (F2).

Layout: ``{root}/{session_id}/{file_id}/content`` plus ``meta.json`` beside it. The
content is written to a temporary file and renamed into place, so a reader never sees
a partial file. Directories are private to the Forge user.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from volundr.domain.presented_files import (
    PresentedFile,
    PresentedFileConflictError,
    PresentedFileStore,
    is_presented_file_id,
)

_DIR_MODE = 0o700
_CONTENT = "content"
_META = "meta.json"


class LocalPresentedFileStore(PresentedFileStore):
    """Store presented files under a local directory."""

    def __init__(self, root: str = "~/.niuu/presented-files") -> None:
        self._root = Path(root).expanduser()

    def _dir(self, session_id: UUID, file_id: str) -> Path:
        if not is_presented_file_id(file_id):
            raise ValueError("invalid presented file id")
        return self._root / str(UUID(str(session_id))) / file_id

    async def put(
        self, session_id: UUID, file_id: str, *, name: str, mime: str, data: bytes
    ) -> PresentedFile:
        return await asyncio.to_thread(self._put, session_id, file_id, name, mime, data)

    async def get(self, session_id: UUID, file_id: str) -> tuple[PresentedFile, Path] | None:
        return await asyncio.to_thread(self._get, session_id, file_id)

    async def delete_session(self, session_id: UUID) -> int:
        return await asyncio.to_thread(self._delete_session, session_id)

    async def total_bytes(self) -> int:
        return await asyncio.to_thread(self._total_bytes)

    def _total_bytes(self) -> int:
        """Sum of stored content files (``{root}/{session}/{file}/content``)."""
        if not self._root.is_dir():
            return 0
        total = 0
        for content in self._root.glob(f"*/*/{_CONTENT}"):
            if content.is_symlink() or not is_presented_file_id(content.parent.name):
                continue
            try:
                total += content.stat().st_size
            except OSError:
                continue  # removed by a concurrent session delete
        return total

    def _delete_session(self, session_id: UUID) -> int:
        target = self._root / str(UUID(str(session_id)))
        if target.is_symlink():
            target.unlink()
            return 0
        if not target.is_dir():
            return 0
        removed = sum(1 for entry in target.iterdir() if is_presented_file_id(entry.name))
        try:
            shutil.rmtree(target)
        except OSError:
            # A write landing mid-delete (ENOTEMPTY); its PUT re-checks the session
            # and deletes again, so one more pass is enough here.
            shutil.rmtree(target, ignore_errors=True)
        return removed

    def _put(
        self, session_id: UUID, file_id: str, name: str, mime: str, data: bytes
    ) -> PresentedFile:
        digest = hashlib.sha256(data).hexdigest()
        existing = self._get(session_id, file_id)
        if existing is not None:
            if existing[0].sha256 != digest:
                raise PresentedFileConflictError(f"{file_id} is already stored with other bytes")
            return existing[0]
        target = self._dir(session_id, file_id)
        target.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        record = PresentedFile(
            session_id=session_id,
            file_id=file_id,
            name=name,
            mime=mime,
            size=len(data),
            sha256=digest,
            stored_at=datetime.now(UTC),
        )
        self._write_atomic(target / _CONTENT, data)
        meta = {
            "name": name,
            "mime": mime,
            "size": record.size,
            "sha256": digest,
            "stored_at": record.stored_at.isoformat(),
        }
        self._write_atomic(target / _META, json.dumps(meta).encode())
        return record

    def _get(self, session_id: UUID, file_id: str) -> tuple[PresentedFile, Path] | None:
        try:
            target = self._dir(session_id, file_id)
        except ValueError:
            return None
        content, meta_path = target / _CONTENT, target / _META
        if content.is_symlink() or meta_path.is_symlink():
            return None
        if not content.is_file() or not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text())
            record = PresentedFile(
                session_id=session_id,
                file_id=file_id,
                name=str(meta["name"]),
                mime=str(meta["mime"]),
                size=int(meta["size"]),
                sha256=str(meta["sha256"]),
                stored_at=datetime.fromisoformat(meta["stored_at"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return record, content

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
