-- Fix sagas table: ensure repos array, name, and status columns exist.
-- The initial schema may already have these columns; this migration is
-- idempotent for both old and new initial schemas.

ALTER TABLE sagas ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS repos TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ACTIVE';

-- Migrate existing data from singular repo column (only if it exists).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sagas' AND column_name = 'repo'
    ) THEN
        UPDATE sagas SET repos = ARRAY[repo] WHERE repo IS NOT NULL AND repo != '';
    END IF;
END $$;

ALTER TABLE sagas DROP COLUMN IF EXISTS repo;
