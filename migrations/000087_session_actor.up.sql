-- The workload that created a session on its owner's behalf (an exchanged token's act.sub).
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS actor_id TEXT;
CREATE INDEX IF NOT EXISTS idx_sessions_owner_actor
    ON sessions (owner_id, actor_id)
    WHERE actor_id IS NOT NULL;
