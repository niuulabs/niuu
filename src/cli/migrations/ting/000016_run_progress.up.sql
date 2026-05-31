-- Unified run progress tracking for all tracker adapters.
-- LinearAdapter stores progress alongside Linear API issue data.
-- NativeAdapter uses the existing runs table (no change needed).

DROP TABLE IF EXISTS dispatched_sessions;

CREATE TABLE IF NOT EXISTS run_progress (
    tracker_id          TEXT PRIMARY KEY,
    run_id             UUID NOT NULL DEFAULT gen_random_uuid(),
    owner_id            TEXT,
    phase_tracker_id    TEXT,
    saga_tracker_id     TEXT,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    session_id          TEXT,
    confidence          FLOAT DEFAULT 0,
    pr_url              TEXT,
    pr_id               TEXT,
    retry_count         INT DEFAULT 0,
    reason              TEXT,
    chronicle_summary   TEXT,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_progress_status ON run_progress(status);
CREATE INDEX IF NOT EXISTS idx_run_progress_session ON run_progress(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_run_progress_run_id ON run_progress(run_id);
CREATE INDEX IF NOT EXISTS idx_run_progress_phase ON run_progress(phase_tracker_id) WHERE phase_tracker_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS run_confidence_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id     UUID NOT NULL,
    tracker_id  TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    delta       FLOAT NOT NULL,
    score_after FLOAT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_confidence_events_tracker ON run_confidence_events(tracker_id);

CREATE TABLE IF NOT EXISTS run_session_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id     UUID NOT NULL,
    tracker_id  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    sender      TEXT NOT NULL DEFAULT 'user',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_session_messages_tracker ON run_session_messages(tracker_id);
CREATE INDEX IF NOT EXISTS idx_run_session_messages_session ON run_session_messages(session_id);
