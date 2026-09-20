ALTER TABLE workflow_executions
    ADD COLUMN IF NOT EXISTS blocker_revision BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS blocker_notified_revision BIGINT NOT NULL DEFAULT 0;

ALTER TABLE workflow_execution_children
    ADD COLUMN IF NOT EXISTS last_polled_at TIMESTAMPTZ;

UPDATE workflow_executions execution
SET blocker_revision = 1
WHERE blocker_revision = 0
  AND execution.state NOT IN ('canceled', 'completed', 'failed')
  AND EXISTS (
      SELECT 1
      FROM workflow_execution_children child
      WHERE child.execution_id = execution.id
        AND child.state IN ('blocked', 'failed')
  );
