-- Admin settings edited on the Settings page (Forge → Storage) persist across
-- restarts instead of living in process memory. One row per settings section.
CREATE TABLE IF NOT EXISTS admin_settings (
    section     TEXT PRIMARY KEY,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
