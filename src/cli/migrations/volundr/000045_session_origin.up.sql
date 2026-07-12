-- Track where a session originated (volundr, claude, codex) and the native
-- CLI session/thread id for sessions imported from an external harness.

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS origin VARCHAR(50) NOT NULL DEFAULT 'volundr';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS external_session_id VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_sessions_external_session_id
    ON sessions (external_session_id)
    WHERE external_session_id IS NOT NULL;
