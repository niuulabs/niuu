DROP TABLE IF EXISTS integration_usage;
DROP INDEX IF EXISTS idx_integration_connections_project;
ALTER TABLE integration_connections DROP COLUMN IF EXISTS connected_by;
ALTER TABLE integration_connections DROP COLUMN IF EXISTS project_id;
