ALTER TABLE workflow_execution_children
    DROP COLUMN IF EXISTS last_polled_at;

ALTER TABLE workflow_executions
    DROP COLUMN IF EXISTS blocker_notified_revision,
    DROP COLUMN IF EXISTS blocker_revision;
