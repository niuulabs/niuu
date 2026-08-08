"""PostgreSQL checkpoint adapter — infra / Kubernetes mode.

Two tables back the two checkpoint categories:

ravn_checkpoints — one row per task_id (crash-recovery, NIU-504):
    Same schema as migration 000030.  New NIU-537 columns added via 000031.

ravn_checkpoint_snapshots — one row per named snapshot (NIU-537):
    checkpoint_id  TEXT PRIMARY KEY  (ckpt_{task_id}_{seq})
    task_id        TEXT NOT NULL
    seq            INTEGER NOT NULL
    label          TEXT NOT NULL DEFAULT ''
    tags           JSONB NOT NULL DEFAULT '[]'
    memory_context TEXT NOT NULL DEFAULT ''
    ... plus all the same payload columns as ravn_checkpoints ...

Raw SQL with asyncpg — no ORM.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from ravn.adapters._pool_sizing import AUX_POOL_MAX_SIZE, AUX_POOL_MIN_SIZE
from ravn.domain.checkpoint import Checkpoint, InterruptReason
from ravn.ports.checkpoint import CheckpointPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_CRASH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ravn_checkpoints (
    task_id              TEXT PRIMARY KEY,
    user_input           TEXT NOT NULL DEFAULT '',
    messages             JSONB NOT NULL DEFAULT '[]',
    todos                JSONB NOT NULL DEFAULT '[]',
    budget_consumed      INTEGER NOT NULL DEFAULT 0,
    budget_total         INTEGER NOT NULL DEFAULT 0,
    last_tool_call       JSONB,
    last_tool_result     JSONB,
    partial_response     TEXT NOT NULL DEFAULT '',
    interrupted_by       TEXT,
    created_at           TIMESTAMPTZ NOT NULL,
    -- NIU-537 extended fields (added via migration 000031)
    checkpoint_id        TEXT NOT NULL DEFAULT '',
    seq                  INTEGER NOT NULL DEFAULT 0,
    label                TEXT NOT NULL DEFAULT '',
    tags                 JSONB NOT NULL DEFAULT '[]',
    memory_context       TEXT NOT NULL DEFAULT ''
);
"""

_CREATE_SNAPSHOT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ravn_checkpoint_snapshots (
    checkpoint_id        TEXT PRIMARY KEY,
    task_id              TEXT NOT NULL,
    seq                  INTEGER NOT NULL,
    label                TEXT NOT NULL DEFAULT '',
    tags                 JSONB NOT NULL DEFAULT '[]',
    memory_context       TEXT NOT NULL DEFAULT '',
    user_input           TEXT NOT NULL DEFAULT '',
    messages             JSONB NOT NULL DEFAULT '[]',
    todos                JSONB NOT NULL DEFAULT '[]',
    budget_consumed      INTEGER NOT NULL DEFAULT 0,
    budget_total         INTEGER NOT NULL DEFAULT 0,
    last_tool_call       JSONB,
    last_tool_result     JSONB,
    partial_response     TEXT NOT NULL DEFAULT '',
    interrupted_by       TEXT,
    created_at           TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ravn_snapshots_task_id
    ON ravn_checkpoint_snapshots (task_id, seq DESC);
"""

# ---------------------------------------------------------------------------
# DML — crash-recovery
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
INSERT INTO ravn_checkpoints (
    task_id, user_input, messages, todos,
    budget_consumed, budget_total,
    last_tool_call, last_tool_result,
    partial_response, interrupted_by, created_at,
    checkpoint_id, seq, label, tags, memory_context
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
ON CONFLICT (task_id) DO UPDATE SET
    user_input       = EXCLUDED.user_input,
    messages         = EXCLUDED.messages,
    todos            = EXCLUDED.todos,
    budget_consumed  = EXCLUDED.budget_consumed,
    budget_total     = EXCLUDED.budget_total,
    last_tool_call   = EXCLUDED.last_tool_call,
    last_tool_result = EXCLUDED.last_tool_result,
    partial_response = EXCLUDED.partial_response,
    interrupted_by   = EXCLUDED.interrupted_by,
    created_at       = EXCLUDED.created_at,
    checkpoint_id    = EXCLUDED.checkpoint_id,
    seq              = EXCLUDED.seq,
    label            = EXCLUDED.label,
    tags             = EXCLUDED.tags,
    memory_context   = EXCLUDED.memory_context;
"""

_SELECT_SQL = """
SELECT task_id, user_input, messages, todos,
       budget_consumed, budget_total,
       last_tool_call, last_tool_result,
       partial_response, interrupted_by, created_at,
       checkpoint_id, seq, label, tags, memory_context
FROM ravn_checkpoints
WHERE task_id = $1;
"""

_DELETE_SQL = "DELETE FROM ravn_checkpoints WHERE task_id = $1;"

_LIST_SQL = "SELECT task_id FROM ravn_checkpoints ORDER BY created_at DESC;"

# ---------------------------------------------------------------------------
# DML — named snapshots
# ---------------------------------------------------------------------------

_NEXT_SEQ_SQL = """
SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq
FROM ravn_checkpoint_snapshots
WHERE task_id = $1;
"""

_INSERT_SNAPSHOT_SQL = """
INSERT INTO ravn_checkpoint_snapshots (
    checkpoint_id, task_id, seq, label, tags, memory_context,
    user_input, messages, todos, budget_consumed, budget_total,
    last_tool_call, last_tool_result, partial_response, interrupted_by, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
ON CONFLICT (checkpoint_id) DO NOTHING;
"""

_LIST_SNAPSHOTS_SQL = """
SELECT checkpoint_id, task_id, seq, label, tags, memory_context,
       user_input, messages, todos, budget_consumed, budget_total,
       last_tool_call, last_tool_result, partial_response, interrupted_by, created_at
FROM ravn_checkpoint_snapshots
WHERE task_id = $1
ORDER BY seq DESC;
"""

_SELECT_SNAPSHOT_SQL = """
SELECT checkpoint_id, task_id, seq, label, tags, memory_context,
       user_input, messages, todos, budget_consumed, budget_total,
       last_tool_call, last_tool_result, partial_response, interrupted_by, created_at
FROM ravn_checkpoint_snapshots
WHERE checkpoint_id = $1;
"""

_DELETE_SNAPSHOT_SQL = "DELETE FROM ravn_checkpoint_snapshots WHERE checkpoint_id = $1;"

_PRUNE_SNAPSHOTS_SQL = """
DELETE FROM ravn_checkpoint_snapshots
WHERE task_id = $1
  AND checkpoint_id NOT IN (
      SELECT checkpoint_id FROM ravn_checkpoint_snapshots
      WHERE task_id = $1
      ORDER BY seq DESC
      LIMIT $2
  );
"""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class PostgresCheckpointAdapter(CheckpointPort):
    """Checkpoint adapter backed by PostgreSQL.

    Parameters
    ----------
    dsn:
        asyncpg-compatible DSN string, e.g.
        ``postgresql://user:pass@host/db``.
    max_snapshots_per_task:
        Maximum named snapshots retained per task.  Oldest are pruned.
    """

    def __init__(self, dsn: str, max_snapshots_per_task: int = 20) -> None:
        self._dsn = dsn
        self._pool: object | None = None
        self._max_snapshots = max_snapshots_per_task

    async def _ensure_pool(self) -> object:
        if self._pool is not None:
            return self._pool
        import asyncpg  # type: ignore[import]

        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=AUX_POOL_MIN_SIZE,
            max_size=AUX_POOL_MAX_SIZE,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_CRASH_TABLE_SQL)
            await conn.execute(_CREATE_SNAPSHOT_TABLE_SQL)
        return self._pool

    # ------------------------------------------------------------------
    # NIU-504: crash-recovery checkpoint
    # ------------------------------------------------------------------

    async def save(self, checkpoint: Checkpoint) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                checkpoint.task_id,
                checkpoint.user_input,
                json.dumps(checkpoint.messages),
                json.dumps(checkpoint.todos),
                checkpoint.iteration_budget_consumed,
                checkpoint.iteration_budget_total,
                json.dumps(checkpoint.last_tool_call) if checkpoint.last_tool_call else None,
                json.dumps(checkpoint.last_tool_result) if checkpoint.last_tool_result else None,
                checkpoint.partial_response,
                checkpoint.interrupted_by,
                checkpoint.created_at,
                checkpoint.checkpoint_id,
                checkpoint.seq,
                checkpoint.label,
                json.dumps(checkpoint.tags),
                checkpoint.memory_context,
            )
        logger.debug("Crash checkpoint saved to postgres: %s", checkpoint.task_id)

    async def load(self, task_id: str) -> Checkpoint | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, task_id)
        if row is None:
            return None
        return _row_to_checkpoint(row)

    async def delete(self, task_id: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(_DELETE_SQL, task_id)

    async def list_task_ids(self) -> list[str]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_LIST_SQL)
        return [r["task_id"] for r in rows]

    # ------------------------------------------------------------------
    # NIU-537: named snapshots
    # ------------------------------------------------------------------

    async def save_snapshot(self, checkpoint: Checkpoint) -> str:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(_NEXT_SEQ_SQL, checkpoint.task_id)
                seq = row["next_seq"]

                checkpoint_id = Checkpoint.make_snapshot_id(checkpoint.task_id, seq)
                checkpoint.checkpoint_id = checkpoint_id
                checkpoint.seq = seq

                await conn.execute(
                    _INSERT_SNAPSHOT_SQL,
                    checkpoint_id,
                    checkpoint.task_id,
                    seq,
                    checkpoint.label,
                    json.dumps(checkpoint.tags),
                    checkpoint.memory_context,
                    checkpoint.user_input,
                    json.dumps(checkpoint.messages),
                    json.dumps(checkpoint.todos),
                    checkpoint.iteration_budget_consumed,
                    checkpoint.iteration_budget_total,
                    json.dumps(checkpoint.last_tool_call) if checkpoint.last_tool_call else None,
                    json.dumps(checkpoint.last_tool_result)
                    if checkpoint.last_tool_result
                    else None,
                    checkpoint.partial_response,
                    checkpoint.interrupted_by,
                    checkpoint.created_at,
                )
                # Prune oldest snapshots if limit exceeded
                await conn.execute(_PRUNE_SNAPSHOTS_SQL, checkpoint.task_id, self._max_snapshots)

        logger.debug("Snapshot saved to postgres: %s", checkpoint_id)
        return checkpoint_id

    async def list_for_task(self, task_id: str) -> list[Checkpoint]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_LIST_SNAPSHOTS_SQL, task_id)
        return [_row_to_snapshot(row) for row in rows]

    async def load_snapshot(self, checkpoint_id: str) -> Checkpoint | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SNAPSHOT_SQL, checkpoint_id)
        if row is None:
            return None
        return _row_to_snapshot(row)

    async def delete_snapshot(self, checkpoint_id: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(_DELETE_SNAPSHOT_SQL, checkpoint_id)


# ---------------------------------------------------------------------------
# Row deserialisation helpers
# ---------------------------------------------------------------------------


def _parse_json(val: str | None) -> object:
    if val is None:
        return None
    if isinstance(val, str):
        return json.loads(val)
    return val  # asyncpg may return already-decoded JSONB


def _parse_ts(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


def _row_to_checkpoint(row: object) -> Checkpoint:
    interrupted_by_raw = row["interrupted_by"]
    interrupted_by = InterruptReason(interrupted_by_raw) if interrupted_by_raw else None

    tags_raw = _parse_json(row["tags"]) if "tags" in row.keys() else []

    return Checkpoint(
        task_id=row["task_id"],
        user_input=row["user_input"] or "",
        messages=_parse_json(row["messages"]) or [],
        todos=_parse_json(row["todos"]) or [],
        iteration_budget_consumed=row["budget_consumed"],
        iteration_budget_total=row["budget_total"],
        last_tool_call=_parse_json(row["last_tool_call"]),
        last_tool_result=_parse_json(row["last_tool_result"]),
        partial_response=row["partial_response"] or "",
        interrupted_by=interrupted_by,
        created_at=_parse_ts(row["created_at"]),
        checkpoint_id=row["checkpoint_id"] if "checkpoint_id" in row.keys() else "",
        seq=row["seq"] if "seq" in row.keys() else 0,
        label=row["label"] if "label" in row.keys() else "",
        tags=tags_raw or [],
        memory_context=row["memory_context"] if "memory_context" in row.keys() else "",
    )


def _row_to_snapshot(row: object) -> Checkpoint:
    interrupted_by_raw = row["interrupted_by"]
    interrupted_by = InterruptReason(interrupted_by_raw) if interrupted_by_raw else None
    tags_raw = _parse_json(row["tags"]) or []

    return Checkpoint(
        task_id=row["task_id"],
        user_input=row["user_input"] or "",
        messages=_parse_json(row["messages"]) or [],
        todos=_parse_json(row["todos"]) or [],
        iteration_budget_consumed=row["budget_consumed"],
        iteration_budget_total=row["budget_total"],
        last_tool_call=_parse_json(row["last_tool_call"]),
        last_tool_result=_parse_json(row["last_tool_result"]),
        partial_response=row["partial_response"] or "",
        interrupted_by=interrupted_by,
        created_at=_parse_ts(row["created_at"]),
        checkpoint_id=row["checkpoint_id"],
        seq=row["seq"],
        label=row["label"] or "",
        tags=tags_raw,
        memory_context=row["memory_context"] or "",
    )
