-- Individual activity acknowledgements supplement, never advance, the host watermark.
CREATE TABLE IF NOT EXISTS forge_notification_reads (
    user_id TEXT NOT NULL,
    notification_id UUID NOT NULL REFERENCES forge_notifications(id) ON DELETE CASCADE,
    read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, notification_id)
);
CREATE INDEX IF NOT EXISTS idx_forge_notification_reads_notification
    ON forge_notification_reads (notification_id);
