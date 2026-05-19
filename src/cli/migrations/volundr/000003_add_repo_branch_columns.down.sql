-- Remove columns added by this migration
-- Note: Only removes columns that weren't in the original minimal schema

DROP INDEX IF EXISTS idx_sessions_last_active;

ALTER TABLE sessions DROP COLUMN IF EXISTS repo;
ALTER TABLE sessions DROP COLUMN IF EXISTS branch;
ALTER TABLE sessions DROP COLUMN IF EXISTS chat_endpoint;
ALTER TABLE sessions DROP COLUMN IF EXISTS code_endpoint;
ALTER TABLE sessions DROP COLUMN IF EXISTS last_active;
ALTER TABLE sessions DROP COLUMN IF EXISTS message_count;
ALTER TABLE sessions DROP COLUMN IF EXISTS tokens_used;
ALTER TABLE sessions DROP COLUMN IF EXISTS pod_name;
ALTER TABLE sessions DROP COLUMN IF EXISTS error;
