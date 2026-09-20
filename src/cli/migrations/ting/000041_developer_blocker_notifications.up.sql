ALTER TABLE developer_executions
    ADD COLUMN IF NOT EXISTS blocker_revision BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS blocker_notified_revision BIGINT NOT NULL DEFAULT 0;

ALTER TABLE developer_execution_children
    ADD COLUMN IF NOT EXISTS last_polled_at TIMESTAMPTZ;

UPDATE developer_executions execution
SET blocker_revision = 1
WHERE blocker_revision = 0
  AND execution.state NOT IN ('canceled', 'completed', 'failed')
  AND EXISTS (
      SELECT 1
      FROM developer_execution_children child
      WHERE child.execution_id = execution.id
        AND child.state IN ('blocked', 'failed')
  );
