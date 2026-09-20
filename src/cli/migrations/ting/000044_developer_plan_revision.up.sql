ALTER TABLE developer_executions
    ADD COLUMN IF NOT EXISTS plan_revision TEXT NOT NULL DEFAULT '';
