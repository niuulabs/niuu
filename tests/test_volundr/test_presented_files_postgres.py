"""The Postgres presented-file store: one quota and one record across Forge replicas.

A cluster Forge runs more than one process (replicas, or the two pods of a rolling
update) over one volume. The record of each copy and the write lock live in Forge's
database, so the quota holds whichever replica takes an upload.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_presented_files import PostgresPresentedFileStore
from volundr.domain.presented_files import (
    PresentedFileConflictError,
    PresentedFileQuota,
    PresentedFileQuotaError,
    PresentedFileStoreUnavailableError,
)

FILE_A = "pf_" + "a" * 32
FILE_B = "pf_" + "b" * 32


class FakeDatabase:
    """Just enough of asyncpg for the store: its four statements and advisory locks."""

    def __init__(self) -> None:
        self.rows: dict[tuple, dict] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.held: list[int] = []

    async def fetchval(self, query: str, *args):
        assert query.startswith("SELECT COALESCE(SUM(size), 0) FROM forge_presented_files")
        return sum(row["size"] for row in self.rows.values())

    async def fetchrow(self, query: str, *args):
        if query.startswith("SELECT"):
            return self.rows.get((args[0], args[1]))
        assert query.startswith("INSERT INTO forge_presented_files")
        key = (args[0], args[1])
        assert key not in self.rows, "the store inserts a copy only once"
        names = ("session_id", "file_id", "name", "mime", "size", "sha256", "stored_at")
        self.rows[key] = dict(zip(names, args, strict=True))
        return self.rows[key]

    async def fetch(self, query: str, *args):
        assert query.startswith("DELETE FROM forge_presented_files")
        gone = [key for key in self.rows if key[0] == args[0]]
        return [{"file_id": self.rows.pop(key)["file_id"]} for key in gone]

    @asynccontextmanager
    async def acquire(self):
        yield _Connection(self)


class _Connection:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database

    async def execute(self, query: str, key: int) -> None:
        lock = self._database.locks.setdefault(key, asyncio.Lock())
        if query == "SELECT pg_advisory_lock($1)":
            await lock.acquire()
            self._database.held.append(key)
            return
        assert query == "SELECT pg_advisory_unlock($1)"
        self._database.held.remove(key)
        lock.release()


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase()


def _store(tmp_path, database) -> PostgresPresentedFileStore:
    return PostgresPresentedFileStore(root=str(tmp_path / "volume"), pool=database)


async def test_roundtrip_is_recorded_in_the_database(tmp_path, database):
    store = _store(tmp_path, database)
    sid = uuid4()
    first = await store.put(sid, FILE_A, name="r.pdf", mime="application/pdf", data=b"%PDF")
    again = await store.put(sid, FILE_A, name="r.pdf", mime="application/pdf", data=b"%PDF")
    assert first == again and first.size == 4
    record, path = await store.get(sid, FILE_A)
    assert record.name == "r.pdf" and path.read_bytes() == b"%PDF"
    assert await store.total_bytes() == 4
    with pytest.raises(PresentedFileConflictError):
        await store.put(sid, FILE_A, name="x", mime="text/plain", data=b"other")
    assert await store.get(uuid4(), FILE_A) is None
    assert await store.get(sid, "../../etc/passwd") is None


async def test_another_replica_serves_what_one_replica_stored(tmp_path, database):
    # Same volume, same database, separate processes: the record is shared.
    writer, reader = _store(tmp_path, database), _store(tmp_path, database)
    sid = uuid4()
    await writer.put(sid, FILE_A, name="a.txt", mime="text/plain", data=b"abc")
    record, path = await reader.get(sid, FILE_A)
    assert record.sha256 and path.read_bytes() == b"abc"
    assert await reader.total_bytes() == 3


async def test_replicas_share_one_quota(tmp_path, database):
    # Two replicas take an upload each at the same moment; only one fits.
    replicas = [_store(tmp_path, database), _store(tmp_path, database)]
    quota = PresentedFileQuota(5)
    sid = uuid4()

    async def upload(store, file_id):
        async with store.exclusive():
            await quota.admit(store, sid, file_id, 4)
            await asyncio.sleep(0)  # the other replica runs while this one writes
            await store.put(sid, file_id, name="n", mime="m", data=b"1234")

    results = await asyncio.gather(
        upload(replicas[0], FILE_A), upload(replicas[1], FILE_B), return_exceptions=True
    )
    refused = [result for result in results if isinstance(result, PresentedFileQuotaError)]
    assert len(refused) == 1 and quota.refusals == 1
    assert await replicas[0].total_bytes() == 4
    assert database.held == []


async def test_the_lock_is_released_when_the_section_fails(tmp_path, database):
    store = _store(tmp_path, database)
    with pytest.raises(RuntimeError):
        async with store.exclusive():
            raise RuntimeError("upload failed")
    assert database.held == []
    async with store.exclusive():
        pass


async def test_delete_session_removes_rows_and_bytes(tmp_path, database):
    store = _store(tmp_path, database)
    sid, other = uuid4(), uuid4()
    await store.put(sid, FILE_A, name="a", mime="m", data=b"aa")
    await store.put(sid, FILE_B, name="b", mime="m", data=b"bbb")
    await store.put(other, FILE_A, name="c", mime="m", data=b"c")
    assert await store.delete_session(sid) == 2
    assert not (tmp_path / "volume" / str(sid)).exists()
    assert await store.get(sid, FILE_A) is None
    assert await store.total_bytes() == 1


async def test_a_recorded_copy_with_missing_bytes_is_loud(tmp_path, database):
    # A replica that mounts another volume (or a recreated PVC) must not read as
    # "not stored": that falls back to the live broker and 404s once it stops.
    store = _store(tmp_path, database)
    sid = uuid4()
    await store.put(sid, FILE_A, name="a", mime="m", data=b"a")
    (tmp_path / "volume" / str(sid) / FILE_A / "content").unlink()
    with pytest.raises(PresentedFileStoreUnavailableError, match="same persistent volume"):
        await store.get(sid, FILE_A)


async def test_capacity_counts_recorded_copies(tmp_path, database, monkeypatch):
    import shutil
    from collections import namedtuple

    store = _store(tmp_path, database)
    await store.put(uuid4(), FILE_A, name="a", mime="m", data=b"x" * 40)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(1000, 960, 60))
    await store.ensure_capacity(100)
    with pytest.raises(PresentedFileStoreUnavailableError, match="larger volume"):
        await store.ensure_capacity(101)


@pytest.mark.parametrize(("root", "pool"), [("/v", None), ("", object()), ("  ", object())])
def test_the_store_needs_a_pool_and_a_root(root, pool):
    with pytest.raises(PresentedFileStoreUnavailableError):
        PostgresPresentedFileStore(root=root, pool=pool)


def test_the_migration_creates_every_column_the_store_reads():
    from pathlib import Path

    from volundr.adapters.outbound import postgres_presented_files

    migrations = Path(__file__).resolve().parents[2] / "migrations"
    up = (migrations / "000100_forge_presented_files.up.sql").read_text()
    down = (migrations / "000100_forge_presented_files.down.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS forge_presented_files" in up
    for column in postgres_presented_files._COLUMNS.split(", "):
        assert f"    {column} " in up
    assert "PRIMARY KEY (session_id, file_id)" in up
    assert "DROP TABLE IF EXISTS forge_presented_files" in down
