-- NIU-537: named checkpoint snapshots table and extended crash-recovery columns.
--
-- Adds NIU-537 fields to the existing ravn_checkpoints table (crash-recovery)
-- and creates the ravn_checkpoint_snapshots table (named snapshots, multiple per task).

-- Extend crash-recovery table with NIU-537 snapshot fields.
ALTER TABLE ravn_checkpoints
    ADD COLUMN IF NOT EXISTS checkpoint_id   TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS seq             INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS label           TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS tags            JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS memory_context  TEXT NOT NULL DEFAULT '';

-- Named snapshots: one row per checkpoint_id (ckpt_{task_id}_{seq}).
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
