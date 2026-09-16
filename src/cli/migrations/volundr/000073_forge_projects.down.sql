DROP TABLE IF EXISTS forge_project_receipts;
DROP TABLE IF EXISTS forge_project_dispatches;
DROP INDEX IF EXISTS idx_sessions_project;
ALTER TABLE sessions DROP COLUMN IF EXISTS coordination;
DROP TABLE IF EXISTS forge_projects;
