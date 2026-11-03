"""Durable presented files for a Forge with more than one process (F2 on a cluster).

The bytes live under ``root`` on a volume every Forge replica mounts: ReadWriteMany, or
ReadWriteOnce when every Forge pod runs on one node (``rollout.colocate``). The record
of each copy lives in ``forge_presented_files``. Every replica agrees on the database,
so it is the truth: a copy exists when its row does, the quota's usage is the sum of
the rows, and :meth:`PostgresPresentedFileStore.exclusive` is a Postgres advisory lock,
so quota admission and writes are serialized across every replica, including the two
pods a rolling update runs side by side.

Layout on the volume: ``{root}/{session_id}/{file_id}/content``, written to a temporary
file and renamed into place before its row is committed, so a row never names a
partial file.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from volundr.adapters.outbound.local_presented_files import LocalPresentedFileStore
from volundr.domain.presented_files import (
    PresentedFile,
    PresentedFileConflictError,
    PresentedFileStoreUnavailableError,
    is_presented_file_id,
)

_CONTENT = "content"
#: Advisory-lock key of the presented-file write section (the bytes of ``pf-quota``).
_LOCK_KEY = int.from_bytes(b"pf-quota", "big")
_COLUMNS = "session_id, file_id, name, mime, size, sha256, stored_at"


class PostgresPresentedFileStore(LocalPresentedFileStore):
    """Bytes on a shared volume, records and the write lock in Forge's database."""

    def __init__(self, root: str, pool: Any = None) -> None:
        if pool is None:
            raise PresentedFileStoreUnavailableError(
                "PostgresPresentedFileStore needs Forge's database pool; it is only "
                "composed by Forge's lifespan, which passes it"
            )
        if not str(root or "").strip():
            raise PresentedFileStoreUnavailableError(
                "PostgresPresentedFileStore needs presented_files.store.root: a "
                "directory on a volume every Forge replica mounts"
            )
        super().__init__(root=root)
        self._pool = pool

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        """Hold the cluster-wide write lock for the section.

        The lock is held by one pooled connection; if the pod dies the connection
        closes and Postgres releases it. Releasing the connection to the pool also
        resets its advisory locks, so a cancelled section never leaves one behind.
        """
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
            try:
                yield
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)

    async def ensure_capacity(self, max_total_bytes: int) -> None:
        used = await self.total_bytes()
        await asyncio.to_thread(self._ensure_capacity, max_total_bytes, used)

    async def total_bytes(self) -> int:
        total = await self._pool.fetchval(
            "SELECT COALESCE(SUM(size), 0) FROM forge_presented_files"
        )
        return int(total)

    async def get(self, session_id: UUID, file_id: str) -> tuple[PresentedFile, Path] | None:
        if not is_presented_file_id(file_id):
            return None
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM forge_presented_files WHERE session_id = $1 AND file_id = $2",
            session_id,
            file_id,
        )
        if row is None:
            return None
        content = self._dir(session_id, file_id) / _CONTENT
        present = await asyncio.to_thread(lambda: content.is_file() and not content.is_symlink())
        if not present:
            raise PresentedFileStoreUnavailableError(
                f"presented file {file_id} of session {session_id} is recorded but its "
                f"bytes are missing from presented_files.store.root {str(self._root)!r}: "
                "every Forge replica must mount the same persistent volume there"
            )
        return _record(row), content

    async def put(
        self, session_id: UUID, file_id: str, *, name: str, mime: str, data: bytes
    ) -> PresentedFile:
        """Store a copy once. Call inside :meth:`exclusive`, as the upload route does."""
        digest = hashlib.sha256(data).hexdigest()
        existing = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM forge_presented_files WHERE session_id = $1 AND file_id = $2",
            session_id,
            file_id,
        )
        if existing is not None:
            if existing["sha256"] != digest:
                raise PresentedFileConflictError(f"{file_id} is already stored with other bytes")
            return _record(existing)
        await asyncio.to_thread(self._write_content, session_id, file_id, data)
        row = await self._pool.fetchrow(
            f"INSERT INTO forge_presented_files ({_COLUMNS}) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            f"RETURNING {_COLUMNS}",
            session_id,
            file_id,
            name,
            mime,
            len(data),
            digest,
            datetime.now(UTC),
        )
        return _record(row)

    async def delete_session(self, session_id: UUID) -> int:
        rows = await self._pool.fetch(
            "DELETE FROM forge_presented_files WHERE session_id = $1 RETURNING file_id",
            session_id,
        )
        await asyncio.to_thread(self._delete_session, session_id)
        return len(rows)

    def _write_content(self, session_id: UUID, file_id: str, data: bytes) -> None:
        target = self._dir(session_id, file_id)
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._write_atomic(target / _CONTENT, data)


def _record(row: Any) -> PresentedFile:
    return PresentedFile(
        session_id=row["session_id"],
        file_id=row["file_id"],
        name=row["name"],
        mime=row["mime"],
        size=int(row["size"]),
        sha256=row["sha256"],
        stored_at=row["stored_at"],
    )
