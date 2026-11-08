DROP INDEX IF EXISTS idx_workflow_steps_undelivered;
ALTER TABLE workflow_steps DROP COLUMN IF EXISTS delivered_at;
