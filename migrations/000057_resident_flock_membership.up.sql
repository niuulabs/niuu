ALTER TABLE resident_runtimes
    ADD COLUMN IF NOT EXISTS flock_id UUID,
    ADD COLUMN IF NOT EXISTS flock_member_id UUID,
    ADD COLUMN IF NOT EXISTS flock_role VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS flock_peer_id VARCHAR(255) NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_resident_runtimes_flock
    ON resident_runtimes (flock_id)
    WHERE flock_id IS NOT NULL;
