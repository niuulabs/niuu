ALTER TABLE workflow_step_flags
    DROP COLUMN IF EXISTS action_error,
    DROP COLUMN IF EXISTS action_done_at,
    DROP COLUMN IF EXISTS action_at;
