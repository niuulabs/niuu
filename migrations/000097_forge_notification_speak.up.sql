-- The speak intent an agent notification may carry (LEX-1536): the words a voice
-- client may say aloud for it and how soon. Forge stores and serves it; it never
-- speaks or delivers it. Both columns are set together or not at all, and never
-- on reply_ready (Forge's own kind).
ALTER TABLE forge_notifications ADD COLUMN IF NOT EXISTS speak_text TEXT NULL;
ALTER TABLE forge_notifications ADD COLUMN IF NOT EXISTS speak_priority VARCHAR(16) NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'forge_notifications_speak_check'
    ) THEN
        ALTER TABLE forge_notifications
            ADD CONSTRAINT forge_notifications_speak_check
            CHECK (
                (speak_text IS NULL AND speak_priority IS NULL)
                OR (
                    speak_text IS NOT NULL
                    AND speak_priority IS NOT NULL
                    AND char_length(speak_text) BETWEEN 1 AND 600
                    AND speak_priority IN ('now', 'next_window')
                    AND kind <> 'reply_ready'
                )
            );
    END IF;
END $$;
