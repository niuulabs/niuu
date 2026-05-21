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
    created_at           TIMESTAMPTZ NOT NULL
);
