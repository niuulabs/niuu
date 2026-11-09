-- Back to steps delivered to their member's session over HTTP.
--
-- Offers and what hangs on them go first: the older kind and flag checks do not know them.

DELETE FROM workflow_steps WHERE kind = 'offer';
DELETE FROM workflow_step_flags WHERE kind = 'unclaimed';

ALTER TABLE workflow_step_flags
    DROP CONSTRAINT IF EXISTS workflow_step_flags_kind_check;
ALTER TABLE workflow_step_flags
    ADD CONSTRAINT workflow_step_flags_kind_check
    CHECK (kind IN ('overdue', 'not_started', 'undelivered', 'unclosed', 'session_gone'));

ALTER TABLE workflow_steps
    DROP CONSTRAINT IF EXISTS workflow_steps_kind_check;
ALTER TABLE workflow_steps
    ADD CONSTRAINT workflow_steps_kind_check
    CHECK (kind IN ('stage_member', 'gate', 'wait', 'end'));

DROP INDEX IF EXISTS idx_workflow_steps_offer;
ALTER TABLE workflow_steps DROP CONSTRAINT IF EXISTS workflow_steps_orchestration_check;
ALTER TABLE workflow_steps
    DROP COLUMN IF EXISTS offer_id,
    DROP COLUMN IF EXISTS picked_up_at,
    DROP COLUMN IF EXISTS orchestration;

ALTER TABLE workflow_steps
    ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;

-- Steps that were active and bound before this column came back were delivered at once.
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
