ALTER TABLE workflows DROP COLUMN IF EXISTS workflow_definitions_json;
ALTER TABLE workflows DROP COLUMN IF EXISTS workflow_dependencies_json;
ALTER TABLE workflows DROP COLUMN IF EXISTS schema_version;
