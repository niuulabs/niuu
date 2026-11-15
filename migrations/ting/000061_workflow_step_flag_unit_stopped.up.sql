-- A step can be flagged because its run's sessions are stopped: an offer that Ting
-- cannot hand to the run's root while the root is stopped, failed or gone.

ALTER TABLE workflow_step_flags
    DROP CONSTRAINT IF EXISTS workflow_step_flags_kind_check;
ALTER TABLE workflow_step_flags
    ADD CONSTRAINT workflow_step_flags_kind_check
    CHECK (kind IN ('overdue', 'unclaimed', 'unclosed', 'session_gone', 'unit_stopped'));
