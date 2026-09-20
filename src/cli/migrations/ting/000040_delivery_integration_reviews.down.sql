DROP INDEX IF EXISTS idx_workflow_executions_parent_session;

ALTER TABLE workflow_executions
    DROP COLUMN IF EXISTS integration_review_event_id,
    DROP COLUMN IF EXISTS integration_review_receipt,
    DROP COLUMN IF EXISTS integration_candidate,
    DROP COLUMN IF EXISTS integration_allocation,
    DROP COLUMN IF EXISTS integration_receipts;
