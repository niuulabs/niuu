ALTER TABLE workflow_executions DROP COLUMN IF EXISTS workflow_snapshot;
ALTER TABLE workflow_executions DROP COLUMN IF EXISTS completed_at;
ALTER TABLE workflow_executions DROP COLUMN IF EXISTS merge_receipt;
