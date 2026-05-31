-- Rollback session archive column

DROP INDEX IF EXISTS idx_sessions_archived_at;
ALTER TABLE sessions DROP COLUMN IF EXISTS archived_at;
