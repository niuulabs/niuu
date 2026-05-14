-- Per-user dispatcher state: owner_id + max_concurrent_runs

ALTER TABLE dispatcher_state ADD COLUMN IF NOT EXISTS owner_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE dispatcher_state ADD COLUMN IF NOT EXISTS max_concurrent_runs INT NOT NULL DEFAULT 3;
CREATE UNIQUE INDEX IF NOT EXISTS idx_dispatcher_state_owner ON dispatcher_state(owner_id);
