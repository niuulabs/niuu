ALTER TABLE workflow_executions
    ADD COLUMN IF NOT EXISTS plan_revision TEXT NOT NULL DEFAULT '';
