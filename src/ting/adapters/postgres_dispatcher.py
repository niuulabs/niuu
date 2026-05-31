"""PostgreSQL implementation of DispatcherRepository."""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg

from ting.domain.models import DispatcherState
from ting.ports.dispatcher_repository import DispatcherRepository

# Defaults used when creating a new dispatcher state row.
_DEFAULT_RUNNING = True
_DEFAULT_THRESHOLD = 0.75
_DEFAULT_MAX_CONCURRENT_RUNS = 3
_DEFAULT_AUTO_CONTINUE = False


class PostgresDispatcherRepository(DispatcherRepository):
    """Dispatcher state persistence backed by asyncpg."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        auto_continue_default: bool | None = None,
    ) -> None:
        self._pool = pool
        self._auto_continue_default = (
            auto_continue_default if auto_continue_default is not None else _DEFAULT_AUTO_CONTINUE
        )

    async def get_or_create(self, owner_id: str) -> DispatcherState:
        row = await self._pool.fetchrow(
            """
            INSERT INTO dispatcher_state
                (owner_id, running, threshold, max_concurrent_runs, auto_continue, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (owner_id)
            DO UPDATE SET owner_id = dispatcher_state.owner_id
            RETURNING *
            """,
            owner_id,
            _DEFAULT_RUNNING,
            _DEFAULT_THRESHOLD,
            _DEFAULT_MAX_CONCURRENT_RUNS,
            self._auto_continue_default,
        )
        return self._row_to_state(row)

    async def update(self, owner_id: str, **fields: object) -> DispatcherState:
        allowed = {"running", "threshold", "max_concurrent_runs", "auto_continue"}
        to_set = {k: v for k, v in fields.items() if k in allowed and v is not None}

        if not to_set:
            return await self.get_or_create(owner_id)

        # Build dynamic SET clause
        set_parts: list[str] = []
        params: list[object] = []
        idx = 1
        for col, val in to_set.items():
            set_parts.append(f"{col} = ${idx}")
            params.append(val)
            idx += 1
        set_parts.append(f"updated_at = ${idx}")
        params.append(datetime.now(UTC))
        idx += 1

        params.append(owner_id)
        sql = (
            f"UPDATE dispatcher_state SET {', '.join(set_parts)} "  # noqa: S608
            f"WHERE owner_id = ${idx} RETURNING *"
        )
        row = await self._pool.fetchrow(sql, *params)
        if row is None:
            return await self.get_or_create(owner_id)
        return self._row_to_state(row)

    async def list_active_owner_ids(self) -> list[str]:
        rows = await self._pool.fetch("SELECT owner_id FROM dispatcher_state WHERE running = TRUE")
        return [row["owner_id"] for row in rows]

    @staticmethod
    def _row_to_state(row: asyncpg.Record) -> DispatcherState:
        return DispatcherState(
            id=row["id"],
            owner_id=row["owner_id"],
            running=row["running"],
            threshold=row["threshold"],
            max_concurrent_runs=row["max_concurrent_runs"],
            auto_continue=row["auto_continue"],
            updated_at=row["updated_at"] or datetime.now(UTC),
        )
