-- Capture the CLI/agent conversation id (Claude session UUID or Codex thread
-- id) reported by the broker at runtime, so a stopped session can be resumed
-- with its prior conversation on restart.

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS cli_session_id VARCHAR(255);
