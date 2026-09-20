DROP INDEX IF EXISTS idx_workflow_executions_parent_stop_pending;

ALTER TABLE workflow_executions
    DROP COLUMN IF EXISTS parent_stopped_at,
    DROP COLUMN IF EXISTS parent_stop_requested_at;
