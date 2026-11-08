-- Recreates the tables dropped by the up migration, empty, as 000026, 000030, 000031,
-- 000032, 000081 and 000082 defined them. Their rows cannot be restored.
CREATE OR REPLACE FUNCTION ravn_episode_tsv(summary TEXT, task_desc TEXT, tags TEXT[])
RETURNS tsvector LANGUAGE sql IMMUTABLE AS $$
    SELECT to_tsvector('english'::regconfig,
        coalesce(summary, '') || ' ' || coalesce(task_desc, '') || ' ' || array_to_string(tags, ' ')
    );
$$;

CREATE TABLE IF NOT EXISTS ravn_episodes (
    episode_id       TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    agent_id         TEXT NOT NULL DEFAULT '',
    timestamp        TIMESTAMP WITH TIME ZONE NOT NULL,
    summary          TEXT NOT NULL,
    task_description TEXT NOT NULL,
    tools_used       TEXT[] NOT NULL DEFAULT '{}',
    outcome          TEXT NOT NULL,
    tags             TEXT[] NOT NULL DEFAULT '{}',
    embedding        TEXT,
    search_vector    tsvector GENERATED ALWAYS AS (
        ravn_episode_tsv(summary, task_description, tags)
    ) STORED,
    reflection       TEXT,
    errors           TEXT,
    cost_usd         NUMERIC(12, 8),
    duration_seconds NUMERIC(12, 3)
);

CREATE INDEX IF NOT EXISTS idx_ravn_episodes_session_id
    ON ravn_episodes (session_id);

CREATE INDEX IF NOT EXISTS idx_ravn_episodes_timestamp
    ON ravn_episodes (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_ravn_episodes_fts
    ON ravn_episodes USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS ravn_sessions (
    session_id    TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL DEFAULT '',
    started_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    ended_at      TIMESTAMP WITH TIME ZONE,
    message_count INTEGER NOT NULL DEFAULT 0,
    token_usage   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ravn_sessions_agent_id
    ON ravn_sessions (agent_id);

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
    checkpoint_id        TEXT NOT NULL DEFAULT '',
    seq                  INTEGER NOT NULL DEFAULT 0,
    label                TEXT NOT NULL DEFAULT '',
    tags                 JSONB NOT NULL DEFAULT '[]',
    memory_context       TEXT NOT NULL DEFAULT ''
);

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

CREATE TABLE IF NOT EXISTS ravn_triggers (
    id           UUID        PRIMARY KEY,
    kind         TEXT        NOT NULL,
    persona_name TEXT        NOT NULL,
    spec         TEXT        NOT NULL,
    repo         TEXT        NOT NULL DEFAULT '',
    enabled      BOOLEAN     NOT NULL DEFAULT TRUE,
    owner_id     TEXT        NOT NULL,
    tenant_id    TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ravn_triggers_tenant_created
    ON ravn_triggers (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ravn_budget_ledger (
    tenant_id  TEXT        NOT NULL,
    ravn_id    TEXT        NOT NULL,
    day        DATE        NOT NULL,
    spent_usd  NUMERIC     NOT NULL DEFAULT 0,
    cap_usd    NUMERIC     NOT NULL DEFAULT 0,
    warn_at    DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, ravn_id, day)
);

CREATE INDEX IF NOT EXISTS idx_ravn_budget_ledger_tenant_day
    ON ravn_budget_ledger (tenant_id, day);
