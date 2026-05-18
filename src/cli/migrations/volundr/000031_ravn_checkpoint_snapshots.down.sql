-- Rollback NIU-537 checkpoint snapshot schema.
DROP TABLE IF EXISTS ravn_checkpoint_snapshots;

ALTER TABLE ravn_checkpoints
    DROP COLUMN IF EXISTS checkpoint_id,
    DROP COLUMN IF EXISTS seq,
    DROP COLUMN IF EXISTS label,
    DROP COLUMN IF EXISTS tags,
    DROP COLUMN IF EXISTS memory_context;
