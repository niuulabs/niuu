"""Connection-holding backends must be closeable, and must actually be closed.

PostgresMemoryAdapter.close() existed for months and was invoked from nowhere:
close() was not on MemoryPort, so the composition root had no contract to call
against and would have needed an isinstance check to know it should. The
checkpoint adapter had no close() at all. Neither leaked in production only
because every deployed resident runs sqlite + file.
"""

from __future__ import annotations

import pytest

from ravn.adapters.checkpoint.disk import DiskCheckpointAdapter
from ravn.adapters.checkpoint.postgres import PostgresCheckpointAdapter
from ravn.adapters.memory.sqlite import SqliteMemoryAdapter
from ravn.ports.checkpoint import CheckpointPort
from ravn.ports.memory import MemoryPort


def test_close_is_on_both_ports_not_just_the_pooled_adapters() -> None:
    # The point of the fix: shutdown can call close() without knowing the
    # backend. If these move back onto the adapters, that guarantee is gone.
    assert callable(MemoryPort.close)
    assert callable(CheckpointPort.close)


@pytest.mark.asyncio
async def test_closing_a_file_backed_memory_adapter_is_a_no_op(tmp_path) -> None:
    memory = SqliteMemoryAdapter(path=str(tmp_path / "memory.db"))

    await memory.close()
    await memory.close()


@pytest.mark.asyncio
async def test_closing_a_disk_checkpoint_adapter_is_a_no_op(tmp_path) -> None:
    port = DiskCheckpointAdapter(checkpoint_dir=str(tmp_path))

    await port.close()
    await port.close()


@pytest.mark.asyncio
async def test_closing_a_postgres_checkpoint_adapter_that_never_connected() -> None:
    # Nothing was opened, so nothing must be awaited — the common case for a
    # CLI run that resumes no checkpoint.
    port = PostgresCheckpointAdapter(dsn="postgresql://unused/db")

    await port.close()


@pytest.mark.asyncio
async def test_closing_a_postgres_checkpoint_adapter_releases_the_pool() -> None:
    closed: list[bool] = []

    class FakePool:
        async def close(self) -> None:
            closed.append(True)

    port = PostgresCheckpointAdapter(dsn="postgresql://unused/db")
    port._pool = FakePool()

    await port.close()
    await port.close()  # idempotent: the second call must not close twice

    assert closed == [True]


def test_checkpoint_pool_is_bounded_well_below_the_asyncpg_default() -> None:
    # asyncpg defaults to min_size=10/max_size=10, so the old bare create_pool
    # grabbed ten connections on first save for a serialised, low-traffic
    # workload.
    port = PostgresCheckpointAdapter(dsn="postgresql://unused/db")

    assert port._pool_min_size == 1
    assert port._pool_max_size == 4
