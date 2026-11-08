UPDATE workflow_campaigns SET status = 'failed' WHERE status = 'canceled';
ALTER TABLE workflow_campaigns DROP CONSTRAINT IF EXISTS workflow_campaigns_status_check;
ALTER TABLE workflow_campaigns ADD CONSTRAINT workflow_campaigns_status_check CHECK (
    status IN ('pending', 'running', 'blocked', 'completed', 'failed')
);
