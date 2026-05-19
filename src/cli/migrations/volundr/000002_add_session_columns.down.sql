-- Rollback session columns

DROP INDEX IF EXISTS idx_sessions_last_active;
ALTER TABLE sessions DROP COLUMN IF EXISTS tokens_used;
ALTER TABLE sessions DROP COLUMN IF EXISTS message_count;
ALTER TABLE sessions DROP COLUMN IF EXISTS last_active;
