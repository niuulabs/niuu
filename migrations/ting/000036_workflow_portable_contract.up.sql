ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS persona_dependencies_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS persona_definitions_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS requirements_json JSONB NOT NULL DEFAULT '[]'::jsonb;
