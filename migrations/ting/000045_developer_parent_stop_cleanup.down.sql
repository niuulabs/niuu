DROP INDEX IF EXISTS idx_developer_executions_parent_stop_pending;

ALTER TABLE developer_executions
    DROP COLUMN IF EXISTS parent_stopped_at,
    DROP COLUMN IF EXISTS parent_stop_requested_at;
