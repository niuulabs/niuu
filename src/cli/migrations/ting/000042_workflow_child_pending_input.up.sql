ALTER TABLE workflow_execution_children
    ADD COLUMN IF NOT EXISTS pending_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS pending_gates JSONB NOT NULL DEFAULT '[]'::jsonb;
