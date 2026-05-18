-- Reverse migration: restore flat repo/branch columns from source JSONB.

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS repo VARCHAR(500) DEFAULT '';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS branch VARCHAR(255) DEFAULT 'main';

-- Backfill from source JSONB (only git sources have repo/branch)
UPDATE sessions
SET repo = COALESCE(source->>'repo', ''),
    branch = COALESCE(source->>'branch', 'main')
WHERE source IS NOT NULL AND source->>'type' = 'git';

ALTER TABLE sessions DROP COLUMN IF EXISTS source;
