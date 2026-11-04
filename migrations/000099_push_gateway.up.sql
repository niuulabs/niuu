-- Apple push for Forge notifications (the Guild push gateway).
--
-- device_tokens: one row per (token, kind). An app install registers its alert
-- token, the update token of each running Live Activity, its push-to-start token
-- and its widget token. `environment` says which APNs host issued the token
-- (development installs: sandbox). `last_push_at`/`push_due` throttle the pushes
-- iOS budgets (widget reloads, push-to-start).
ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'alert';
ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'production';
ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS activity_id TEXT;
ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS last_push_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS push_due BOOLEAN NOT NULL DEFAULT FALSE;

-- A token belongs to whoever is signed in on that install now, so it is unique
-- by itself (per kind), not per owner. Keep the newest row of a token two owners
-- registered before this migration.
DELETE FROM device_tokens older
USING device_tokens newer
WHERE older.token = newer.token
  AND older.kind = newer.kind
  AND (older.updated_at, older.id) < (newer.updated_at, newer.id);
ALTER TABLE device_tokens DROP CONSTRAINT IF EXISTS device_tokens_owner_id_token_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_device_tokens_token_kind ON device_tokens (token, kind);
CREATE INDEX IF NOT EXISTS idx_device_tokens_owner_kind ON device_tokens (owner_id, kind);
CREATE INDEX IF NOT EXISTS idx_device_tokens_push_due ON device_tokens (kind) WHERE push_due;

-- Where the gateway stopped reading each Guild node's notification feed:
-- {instance_id: seq}. One row ('guild').
CREATE TABLE IF NOT EXISTS forge_push_gateway_state (
    id TEXT PRIMARY KEY,
    positions JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- The gateway's alert outbox: one row per notification of any Guild node, inserted
-- 'pending' in the transaction that advances the positions. `attempts` fences the
-- settle of a claimed row; `next_attempt_at` is both the retry time and the lease.
CREATE TABLE IF NOT EXISTS forge_push_deliveries (
    instance_id TEXT NOT NULL,
    notification_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    notified_at TIMESTAMP WITH TIME ZONE NULL,
    payload JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'suppressed', 'skipped', 'failed')),
    detail TEXT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    settled_at TIMESTAMP WITH TIME ZONE NULL,
    PRIMARY KEY (instance_id, notification_id),
    CHECK (status = 'pending' OR settled_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_forge_push_deliveries_due
    ON forge_push_deliveries (next_attempt_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_forge_push_deliveries_sent
    ON forge_push_deliveries (owner_id, settled_at) WHERE status = 'sent';
CREATE INDEX IF NOT EXISTS idx_forge_push_deliveries_settled
    ON forge_push_deliveries (settled_at) WHERE status <> 'pending';
