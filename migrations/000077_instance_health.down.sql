ALTER TABLE niuu_instances DROP CONSTRAINT IF EXISTS niuu_instances_health_check;
ALTER TABLE niuu_instances DROP COLUMN IF EXISTS last_error;
ALTER TABLE niuu_instances DROP COLUMN IF EXISTS last_checked_at;
ALTER TABLE niuu_instances DROP COLUMN IF EXISTS last_seen_at;
ALTER TABLE niuu_instances DROP COLUMN IF EXISTS health;
