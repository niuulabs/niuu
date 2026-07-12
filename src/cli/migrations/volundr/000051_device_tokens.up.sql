-- Push device registrations for session attention notifications.
-- A session entering awaiting_input fans a push out to every device its owner
-- has registered (the iOS app/widget, etc.).
CREATE TABLE IF NOT EXISTS device_tokens (
    id            UUID PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    platform      TEXT NOT NULL,
    token         TEXT NOT NULL,
    app_bundle_id TEXT,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (owner_id, token)
);

CREATE INDEX IF NOT EXISTS idx_device_tokens_owner ON device_tokens (owner_id);
