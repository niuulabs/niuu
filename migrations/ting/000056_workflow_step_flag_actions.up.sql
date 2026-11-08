-- The one automatic action a stuck-step flag may trigger (NIU-1181).
--
-- Starting or re-delivering a step that never reached its session is tried once per
-- flag: retrying an identical request that failed deterministically changes nothing.
-- action_at is the claim (set by a conditional UPDATE, so two sweeps or replicas cannot
-- both try), action_done_at is when it finished, and action_error is why it failed
-- ('' when it did not). A person then retries or reassigns the step.

ALTER TABLE workflow_step_flags
    ADD COLUMN IF NOT EXISTS action_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS action_done_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS action_error TEXT NOT NULL DEFAULT '';
