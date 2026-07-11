ALTER TABLE resident_runtimes
    DROP COLUMN IF EXISTS cost,
    DROP COLUMN IF EXISTS tokens_used,
    DROP COLUMN IF EXISTS message_count;
