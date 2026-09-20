ALTER TABLE workflow_executions
    ADD COLUMN IF NOT EXISTS parent_stop_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS parent_stopped_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_workflow_executions_parent_stop_pending
    ON workflow_executions (parent_stop_requested_at, id)
    WHERE parent_stop_requested_at IS NOT NULL AND parent_stopped_at IS NULL;
