DROP INDEX IF EXISTS idx_resident_runtimes_flock;

ALTER TABLE resident_runtimes
    DROP COLUMN IF EXISTS flock_peer_id,
    DROP COLUMN IF EXISTS flock_role,
    DROP COLUMN IF EXISTS flock_member_id,
    DROP COLUMN IF EXISTS flock_id;
