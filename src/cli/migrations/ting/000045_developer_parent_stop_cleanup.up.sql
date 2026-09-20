ALTER TABLE developer_executions
    ADD COLUMN IF NOT EXISTS parent_stop_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS parent_stopped_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_developer_executions_parent_stop_pending
    ON developer_executions (parent_stop_requested_at, id)
    WHERE parent_stop_requested_at IS NOT NULL AND parent_stopped_at IS NULL;
