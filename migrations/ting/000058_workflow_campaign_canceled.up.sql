-- A run an operator aborted ends 'canceled', its own ending, not 'failed'.

ALTER TABLE workflow_campaigns DROP CONSTRAINT IF EXISTS workflow_campaigns_status_check;
ALTER TABLE workflow_campaigns ADD CONSTRAINT workflow_campaigns_status_check CHECK (
    status IN ('pending', 'running', 'blocked', 'completed', 'failed', 'canceled')
);
