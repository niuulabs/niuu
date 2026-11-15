DROP INDEX IF EXISTS idx_sessions_hosted_port;
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_hosted_pair_check;
ALTER TABLE sessions DROP COLUMN IF EXISTS hosted_port;
ALTER TABLE sessions DROP COLUMN IF EXISTS hosted_by;
