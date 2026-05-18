-- Rollback volundr_presets table and preset_id column

ALTER TABLE sessions DROP COLUMN IF EXISTS preset_id;

DROP TABLE IF EXISTS volundr_presets;
