-- Trackerless workflow campaign records for long-running launched workflows.

CREATE TABLE IF NOT EXISTS workflow_campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workflow_id UUID NOT NULL,
    workflow_version TEXT NOT NULL DEFAULT 'draft',
    workflow_name TEXT NOT NULL DEFAULT '',
    workflow_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    session_id TEXT NOT NULL,
    session_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    active_stage_id TEXT,
    stage_state JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CONSTRAINT workflow_campaigns_status_check CHECK (
        status IN ('pending', 'running', 'blocked', 'completed', 'failed')
    )
);

CREATE INDEX IF NOT EXISTS idx_workflow_campaigns_owner_updated_at
    ON workflow_campaigns(owner_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_workflow_campaigns_status_updated_at
    ON workflow_campaigns(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_workflow_campaigns_session_id
    ON workflow_campaigns(session_id);
