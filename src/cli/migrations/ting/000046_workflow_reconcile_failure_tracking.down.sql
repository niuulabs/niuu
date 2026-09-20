ALTER TABLE workflow_executions
    DROP COLUMN IF EXISTS parent_stop_error,
    DROP COLUMN IF EXISTS parent_stop_attempts;

ALTER TABLE workflow_execution_children
    DROP COLUMN IF EXISTS reconcile_failure_count;
