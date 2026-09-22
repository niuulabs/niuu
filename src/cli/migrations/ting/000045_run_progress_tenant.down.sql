DROP INDEX IF EXISTS idx_run_progress_work_scope;
ALTER TABLE run_progress DROP COLUMN IF EXISTS tenant_id;
