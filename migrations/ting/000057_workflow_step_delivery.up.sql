-- When a stage-member step was handed to its session.
--
-- A step whose session is still booting stays active and bound but undelivered until
-- the session is up. delivered_at makes that durable: a restarted Ting finds the steps
-- it still has to deliver, and the conditional UPDATE that sets it is the claim, so of
-- two workers or replicas waiting on the same step only one delivers it. save_step does
-- not write this column, so recording a step never clears a delivery.

ALTER TABLE workflow_steps
    ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;

-- Steps that were active and bound before this column existed were delivered at once
-- (the engine failed a step whose delivery failed), so they are not delivered again.
UPDATE workflow_steps
    SET delivered_at = COALESCE(activated_at, created_at)
    WHERE delivered_at IS NULL
      AND kind = 'stage_member'
      AND state IN ('active', 'waiting')
      AND session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workflow_steps_undelivered
    ON workflow_steps (run_id)
    WHERE state = 'active' AND kind = 'stage_member'
      AND session_id IS NOT NULL AND delivered_at IS NULL;
