-- Agent-maintained session descriptions. Written only by the targeted description
-- update, never by the generic session update, so a stale in-memory copy cannot clobber them.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS short_description TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS short_description_updated_at TIMESTAMPTZ;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS description_updated_at TIMESTAMPTZ;
