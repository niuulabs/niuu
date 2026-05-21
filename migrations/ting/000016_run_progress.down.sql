DROP TABLE IF EXISTS run_session_messages;
DROP TABLE IF EXISTS run_confidence_events;
DROP TABLE IF EXISTS run_progress;

-- Restore the dispatched_sessions table that migration 000016 up dropped.
CREATE TABLE IF NOT EXISTS dispatched_sessions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        TEXT NOT NULL UNIQUE,
    owner_id          TEXT NOT NULL,
    saga_id           UUID NOT NULL,
    tracker_issue_id  TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'running',
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dispatched_sessions_status ON dispatched_sessions(status);
CREATE INDEX IF NOT EXISTS idx_dispatched_sessions_owner  ON dispatched_sessions(owner_id);
