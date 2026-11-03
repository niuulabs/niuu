DROP INDEX IF EXISTS idx_realms_project_id;
ALTER TABLE realms DROP COLUMN IF EXISTS project_id;
