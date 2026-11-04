DROP INDEX IF EXISTS idx_sagas_project_id;
ALTER TABLE sagas DROP COLUMN IF EXISTS project_id;
