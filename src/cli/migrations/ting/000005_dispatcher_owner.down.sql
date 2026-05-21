-- Revert per-user dispatcher state

DROP INDEX IF EXISTS idx_dispatcher_state_owner;
ALTER TABLE dispatcher_state DROP COLUMN IF EXISTS max_concurrent_runs;
ALTER TABLE dispatcher_state DROP COLUMN IF EXISTS owner_id;
