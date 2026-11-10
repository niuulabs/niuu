-- Back to the flag kinds before a run's stopped sessions were flagged.

DELETE FROM workflow_step_flags WHERE kind = 'unit_stopped';
ALTER TABLE workflow_step_flags
    DROP CONSTRAINT IF EXISTS workflow_step_flags_kind_check;
ALTER TABLE workflow_step_flags
    ADD CONSTRAINT workflow_step_flags_kind_check
    CHECK (kind IN ('overdue', 'unclaimed', 'unclosed', 'session_gone'));
