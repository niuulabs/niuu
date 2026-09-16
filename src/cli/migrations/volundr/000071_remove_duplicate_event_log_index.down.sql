CREATE INDEX IF NOT EXISTS idx_session_event_log_session_seq
    ON session_event_log (session_id, seq);
