-- Rollback source, integration_ids, and setup_scripts from presets

ALTER TABLE volundr_presets DROP COLUMN IF EXISTS setup_scripts;
ALTER TABLE volundr_presets DROP COLUMN IF EXISTS integration_ids;
ALTER TABLE volundr_presets DROP COLUMN IF EXISTS source;
