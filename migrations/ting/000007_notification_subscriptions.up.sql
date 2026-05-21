-- Notification subscriptions table

CREATE TABLE IF NOT EXISTS notification_subscriptions (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   TEXT        NOT NULL,
    channel    TEXT        NOT NULL,
    config     JSONB       NOT NULL DEFAULT '{}',
    enabled    BOOLEAN     NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notification_subs_owner
    ON notification_subscriptions(owner_id);
