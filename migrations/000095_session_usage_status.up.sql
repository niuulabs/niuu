-- LEX-1421: project usage once at durable log ingest; status/list never scan history.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_usage JSONB;
ALTER TABLE session_event_log ADD COLUMN IF NOT EXISTS usage_key TEXT;
ALTER TABLE session_event_log ADD COLUMN IF NOT EXISTS usage JSONB;
CREATE INDEX IF NOT EXISTS idx_session_event_log_usage_key
    ON session_event_log (session_id, usage_key, seq DESC) WHERE usage_key IS NOT NULL;
