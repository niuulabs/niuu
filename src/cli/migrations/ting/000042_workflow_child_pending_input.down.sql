ALTER TABLE workflow_execution_children
    DROP COLUMN IF EXISTS pending_gates,
    DROP COLUMN IF EXISTS pending_questions;
