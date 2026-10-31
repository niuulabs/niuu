DROP INDEX IF EXISTS idx_sessions_owner_actor;
ALTER TABLE sessions DROP COLUMN IF EXISTS actor_id;
