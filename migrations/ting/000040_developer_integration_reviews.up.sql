ALTER TABLE developer_executions
    ADD COLUMN IF NOT EXISTS integration_receipts JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS integration_allocation JSONB,
    ADD COLUMN IF NOT EXISTS integration_candidate JSONB,
    ADD COLUMN IF NOT EXISTS integration_review_receipt JSONB,
    ADD COLUMN IF NOT EXISTS integration_review_event_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_developer_executions_parent_session
    ON developer_executions (owner_id, parent_session_id)
    WHERE state NOT IN ('canceled', 'completed', 'failed');
