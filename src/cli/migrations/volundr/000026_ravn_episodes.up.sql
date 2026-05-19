-- Ravn episodic memory tables.
-- ravn_episodes: stores one row per recorded agent episode.
-- ravn_sessions: stores session lifecycle metadata (populated by future work).

-- Attempt to enable pgvector for embedding similarity support.
-- Silently ignored when the extension is not installed.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector not available — embedding similarity disabled';
END;
$$;

-- Immutable wrapper around to_tsvector for use in generated columns.
-- to_tsvector is STABLE (not IMMUTABLE) in PostgreSQL, but with a fixed
-- regconfig the result is deterministic. Declaring IMMUTABLE is the
-- standard pattern for generated tsvector columns.
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
    -- Stored as a JSON text array (e.g. "[0.1, 0.2, ...]") for portability.
    -- Cast to vector type when pgvector is available: embedding::vector
    embedding        TEXT,
    search_vector    tsvector GENERATED ALWAYS AS (
        ravn_episode_tsv(summary, task_description, tags)
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_ravn_episodes_session_id
    ON ravn_episodes (session_id);

CREATE INDEX IF NOT EXISTS idx_ravn_episodes_timestamp
    ON ravn_episodes (timestamp DESC);

-- GIN index on the generated tsvector column for fast FTS queries.
CREATE INDEX IF NOT EXISTS idx_ravn_episodes_fts
    ON ravn_episodes USING GIN (search_vector);

-- Session lifecycle metadata (populated when Ravn tracks session boundaries).
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
