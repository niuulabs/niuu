ALTER TABLE developer_execution_children
    DROP COLUMN IF EXISTS pending_gates,
    DROP COLUMN IF EXISTS pending_questions;
