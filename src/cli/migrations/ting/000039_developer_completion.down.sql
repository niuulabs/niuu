ALTER TABLE developer_executions DROP COLUMN IF EXISTS workflow_snapshot;
ALTER TABLE developer_executions DROP COLUMN IF EXISTS completed_at;
ALTER TABLE developer_executions DROP COLUMN IF EXISTS merge_receipt;
