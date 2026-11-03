ALTER TABLE forge_notifications DROP CONSTRAINT IF EXISTS forge_notifications_speak_check;
ALTER TABLE forge_notifications DROP COLUMN IF EXISTS speak_priority;
ALTER TABLE forge_notifications DROP COLUMN IF EXISTS speak_text;
