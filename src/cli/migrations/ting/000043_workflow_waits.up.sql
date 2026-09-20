CREATE TABLE IF NOT EXISTS workflow_waits (
    id UUID PRIMARY KEY,
    execution_id UUID NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    execution_generation INTEGER NOT NULL CHECK (execution_generation >= 0),
    execution_revision INTEGER NOT NULL CHECK (execution_revision > 0),
    candidate_digest TEXT NOT NULL CHECK (candidate_digest ~ '^[a-f0-9]{64}$'),
    mode TEXT NOT NULL CHECK (mode IN ('checks', 'merge')),
    request_digest TEXT NOT NULL CHECK (request_digest ~ '^[a-f0-9]{64}$'),
    request JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'ready', 'failed', 'notified')),
    observation JSONB,
    next_poll_at TIMESTAMPTZ NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_token UUID,
    fencing_generation BIGINT NOT NULL DEFAULT 0 CHECK (fencing_generation >= 0),
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notified_at TIMESTAMPTZ,
    UNIQUE (execution_id, request_digest)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_waits_active_generation
    ON workflow_waits (execution_id, execution_generation)
    WHERE state <> 'notified';

CREATE INDEX IF NOT EXISTS idx_workflow_waits_due
    ON workflow_waits (state, next_poll_at, lease_expires_at)
    WHERE state IN ('pending', 'ready', 'failed');
