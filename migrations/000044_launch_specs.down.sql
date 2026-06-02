ALTER TABLE sessions RENAME COLUMN launch_spec_id TO preset_id;

ALTER INDEX IF EXISTS idx_volundr_launch_specs_is_default RENAME TO idx_volundr_presets_is_default;
ALTER INDEX IF EXISTS idx_volundr_launch_specs_cli_tool RENAME TO idx_volundr_presets_cli_tool;
ALTER INDEX IF EXISTS idx_volundr_launch_specs_name RENAME TO idx_volundr_presets_name;

ALTER TABLE volundr_launch_specs DROP COLUMN IF EXISTS workspace_layout;
ALTER TABLE volundr_launch_specs DROP COLUMN IF EXISTS repos;
ALTER TABLE volundr_launch_specs DROP COLUMN IF EXISTS session_definition;

ALTER TABLE volundr_launch_specs RENAME TO volundr_presets;
