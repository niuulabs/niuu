DROP INDEX IF EXISTS idx_forge_notifications_unresolved_correlation;
ALTER TABLE forge_notifications DROP COLUMN IF EXISTS resolution;
ALTER TABLE forge_notifications DROP COLUMN IF EXISTS resolved_by_notification_id;
ALTER TABLE forge_notifications DROP COLUMN IF EXISTS resolved_at;
