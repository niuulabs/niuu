CREATE TABLE IF NOT EXISTS session_messages (
    id          UUID PRIMARY KEY,
    run_id     UUID NOT NULL REFERENCES runs(id),
    session_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    sender      TEXT NOT NULL DEFAULT 'user',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_session_messages_run_id
    ON session_messages (run_id);

CREATE INDEX IF NOT EXISTS idx_session_messages_session_id
    ON session_messages (session_id);
