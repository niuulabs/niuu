DROP INDEX IF EXISTS idx_session_event_log_usage_key;
ALTER TABLE session_event_log DROP COLUMN IF EXISTS usage;
ALTER TABLE session_event_log DROP COLUMN IF EXISTS usage_key;
ALTER TABLE sessions DROP COLUMN IF EXISTS session_usage;
