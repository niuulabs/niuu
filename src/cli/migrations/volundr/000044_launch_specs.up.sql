-- Consolidate profiles + templates + presets into unified launch specs.
-- User-scope launch specs were the former presets table; system-scope specs
-- are config-seeded and not stored here.

ALTER TABLE volundr_presets RENAME TO volundr_launch_specs;

ALTER TABLE volundr_launch_specs ADD COLUMN IF NOT EXISTS session_definition VARCHAR(255);
ALTER TABLE volundr_launch_specs ADD COLUMN IF NOT EXISTS repos JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE volundr_launch_specs ADD COLUMN IF NOT EXISTS workspace_layout JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER INDEX IF EXISTS idx_volundr_presets_name RENAME TO idx_volundr_launch_specs_name;
ALTER INDEX IF EXISTS idx_volundr_presets_cli_tool RENAME TO idx_volundr_launch_specs_cli_tool;
ALTER INDEX IF EXISTS idx_volundr_presets_is_default RENAME TO idx_volundr_launch_specs_is_default;

ALTER TABLE sessions RENAME COLUMN preset_id TO launch_spec_id;
