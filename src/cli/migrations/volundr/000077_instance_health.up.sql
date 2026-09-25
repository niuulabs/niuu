-- Server-side reachability tracking for registered runtime instances. Guild
-- probes each instance on register and on a periodic loop, and records the
-- result here so an offline node is reported as such instead of looking like
-- an idle one that simply has no sessions.
ALTER TABLE niuu_instances ADD COLUMN IF NOT EXISTS health TEXT NOT NULL DEFAULT 'unknown';
-- last_seen_at: the last time a probe SUCCEEDED — "when did it last work".
-- last_checked_at: the last time a probe was ATTEMPTED, success or not —
-- "when did we last look". A never-reachable instance has a last_checked_at
-- but no last_seen_at; conflating the two would let the UI invent a "just
-- now" last-seen time for a node that has never actually answered.
ALTER TABLE niuu_instances ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE niuu_instances ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;
ALTER TABLE niuu_instances ADD COLUMN IF NOT EXISTS last_error TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'niuu_instances_health_check'
    ) THEN
        ALTER TABLE niuu_instances
            ADD CONSTRAINT niuu_instances_health_check
            CHECK (health IN ('unknown', 'ok', 'unreachable'));
    END IF;
END $$;
