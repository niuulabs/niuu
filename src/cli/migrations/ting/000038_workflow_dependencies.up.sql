ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS workflow_dependencies_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS workflow_definitions_json JSONB NOT NULL DEFAULT '{}'::jsonb;
