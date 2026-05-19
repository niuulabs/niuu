-- NIU-574: Add reflection, errors, cost_usd, duration_seconds columns to ravn_episodes.
-- These fields were previously stored in a separate task_outcomes table and are now
-- merged directly into the episode record for a single unified store.

ALTER TABLE ravn_episodes
    ADD COLUMN IF NOT EXISTS reflection       TEXT,
    ADD COLUMN IF NOT EXISTS errors           TEXT,
    ADD COLUMN IF NOT EXISTS cost_usd         NUMERIC(12, 8),
    ADD COLUMN IF NOT EXISTS duration_seconds NUMERIC(12, 3);
