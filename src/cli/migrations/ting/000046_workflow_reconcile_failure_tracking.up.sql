-- Durable, visible failure tracking so one poisoned reconcile item cannot
-- starve the queue: a consecutive-failure counter per child (reset on a
-- successful poll, terminal-failed once it exceeds the configured maximum),
-- and an attempt/error trail for the durable parent-stop cleanup loop.

ALTER TABLE workflow_execution_children
    ADD COLUMN IF NOT EXISTS reconcile_failure_count INTEGER NOT NULL DEFAULT 0
        CHECK (reconcile_failure_count >= 0);

ALTER TABLE workflow_executions
    ADD COLUMN IF NOT EXISTS parent_stop_attempts INTEGER NOT NULL DEFAULT 0
        CHECK (parent_stop_attempts >= 0),
    ADD COLUMN IF NOT EXISTS parent_stop_error TEXT NOT NULL DEFAULT '';
