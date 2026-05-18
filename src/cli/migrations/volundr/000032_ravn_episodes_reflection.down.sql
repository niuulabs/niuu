-- Rollback NIU-574: remove reflection columns from ravn_episodes.

ALTER TABLE ravn_episodes
    DROP COLUMN IF EXISTS reflection,
    DROP COLUMN IF EXISTS errors,
    DROP COLUMN IF EXISTS cost_usd,
    DROP COLUMN IF EXISTS duration_seconds;
