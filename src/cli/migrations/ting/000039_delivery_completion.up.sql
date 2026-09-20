ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS merge_receipt JSONB;
ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE workflow_executions ADD COLUMN IF NOT EXISTS workflow_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;
