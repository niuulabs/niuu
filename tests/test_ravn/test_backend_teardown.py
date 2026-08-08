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


@pytest.mark.asyncio
async def test_an_unimportable_memory_backend_fails_loudly() -> None:
    """A typo in memory.backend used to disable memory with one warning line.

    The resident then recorded nothing and recalled nothing, indefinitely, and
    looked healthy while doing it. 'none' is how you ask for that on purpose.
    """
    from ravn.cli.runtime_builders import _build_memory
    from ravn.config import Settings

    settings = Settings()
    settings.memory.backend = "ravn.adapters.memory.nonexistent.NoSuchAdapter"

    with pytest.raises(ValueError, match="not importable"):
        _build_memory(settings)


def test_none_backend_still_disables_memory_quietly() -> None:
    from ravn.cli.runtime_builders import _build_memory
    from ravn.config import Settings

    settings = Settings()
    settings.memory.backend = "none"

    assert _build_memory(settings) is None
