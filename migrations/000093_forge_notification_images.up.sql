-- Durable copies of images attached to Forge notifications. A row is created
-- 'pending' in the same transaction as its notification. Forge copies the bytes
-- once (through the session broker for a present-file id, by HTTP GET for a URL)
-- and settles the row 'ready' (original and thumbnail stored) or 'failed' (the
-- reason in error). attempts/lease_until fence the copy like the delivery outbox.
CREATE TABLE IF NOT EXISTS forge_notification_images (
    notification_id UUID PRIMARY KEY REFERENCES forge_notifications(id) ON DELETE CASCADE,
    source_kind VARCHAR(16) NOT NULL CHECK (source_kind IN ('file_id', 'url')),
    source_ref TEXT NOT NULL,
    alt TEXT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'ready', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_until TIMESTAMPTZ NULL,
    mime VARCHAR(64) NULL,
    width INTEGER NULL CHECK (width > 0),
    height INTEGER NULL CHECK (height > 0),
    content BYTEA NULL,
    thumbnail_mime VARCHAR(64) NULL,
    thumbnail_width INTEGER NULL CHECK (thumbnail_width > 0),
    thumbnail_height INTEGER NULL CHECK (thumbnail_height > 0),
    thumbnail BYTEA NULL,
    error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        state <> 'ready'
        OR (mime IS NOT NULL AND width IS NOT NULL AND height IS NOT NULL
            AND content IS NOT NULL AND thumbnail_mime IS NOT NULL
            AND thumbnail_width IS NOT NULL AND thumbnail_height IS NOT NULL
            AND thumbnail IS NOT NULL)
    ),
    CHECK (state <> 'failed' OR error IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_forge_notification_images_pending
    ON forge_notification_images (created_at) WHERE state = 'pending';
