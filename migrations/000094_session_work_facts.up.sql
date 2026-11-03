-- G10 (F12d): work facts kept at log ingest, so every reader derives "working" the same way.
-- last_work_at is the newest source time of a work row; open_tool_count counts PreToolUse
-- hooks without their PostToolUse/PostToolUseFailure (reset by result/Stop).
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_work_at TIMESTAMPTZ;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS open_tool_count INTEGER NOT NULL DEFAULT 0;
