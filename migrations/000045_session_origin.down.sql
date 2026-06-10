DROP INDEX IF EXISTS idx_sessions_external_session_id;

ALTER TABLE sessions DROP COLUMN IF EXISTS external_session_id;
ALTER TABLE sessions DROP COLUMN IF EXISTS origin;
