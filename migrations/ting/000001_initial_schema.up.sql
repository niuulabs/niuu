-- Initial schema for ting saga coordinator

CREATE TABLE IF NOT EXISTS sagas (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tracker_id    TEXT NOT NULL,
    tracker_type  TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    repos         TEXT[] NOT NULL DEFAULT '{}',
    confidence    FLOAT DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS phases (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    saga_id       UUID NOT NULL REFERENCES sagas(id),
    tracker_id    TEXT NOT NULL,
    number        INT NOT NULL,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'GATED',
    confidence    FLOAT DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_phases_saga_id ON phases(saga_id);

CREATE TABLE IF NOT EXISTS runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phase_id      UUID NOT NULL REFERENCES phases(id),
    tracker_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    declared_files TEXT[],
    estimate_hours FLOAT,
    status        TEXT NOT NULL DEFAULT 'PENDING',
    confidence    FLOAT DEFAULT 0,
    session_id    TEXT,
    branch        TEXT,
    chronicle_summary TEXT,
    retry_count   INT DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_runs_phase_id ON runs(phase_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS confidence_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID NOT NULL REFERENCES runs(id),
    event_type    TEXT NOT NULL,
    delta         FLOAT NOT NULL,
    score_after   FLOAT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_confidence_events_run_id ON confidence_events(run_id);

CREATE TABLE IF NOT EXISTS dispatcher_state (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    running       BOOL DEFAULT TRUE,
    threshold     FLOAT DEFAULT 0.75,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
