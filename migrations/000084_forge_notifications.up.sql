-- Forge session notifications. A cursor-ordered feed projected from the durable
-- session log (agent notification turns, reply_ready, attention) and direct submits,
-- a per-reader read watermark, owner delivery rules and the delivery outbox.
-- No FK to sessions: like session_event_log, notifications outlive their session.
CREATE TABLE IF NOT EXISTS forge_notifications (
    id UUID PRIMARY KEY,
    seq BIGSERIAL NOT NULL UNIQUE,
    dedupe_key TEXT NOT NULL UNIQUE,
    session_id UUID NULL,
    session_seq BIGINT NULL,
    session_name TEXT NULL,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NULL,
    project_id TEXT NULL,
    kind VARCHAR(32) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    source VARCHAR(16) NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    links JSONB NOT NULL DEFAULT '[]',
    engine TEXT NULL,
    model TEXT NULL,
    correlation_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_forge_notifications_owner_seq
    ON forge_notifications (owner_id, seq);
CREATE INDEX IF NOT EXISTS idx_forge_notifications_session_seq
    ON forge_notifications (session_id, seq);
CREATE INDEX IF NOT EXISTS idx_forge_notifications_project_seq
    ON forge_notifications (project_id, seq);
CREATE INDEX IF NOT EXISTS idx_forge_notifications_kind_seq
    ON forge_notifications (kind, seq);

CREATE TABLE IF NOT EXISTS forge_notification_read_states (
    user_id TEXT PRIMARY KEY,
    read_through_seq BIGINT NOT NULL DEFAULT 0 CHECK (read_through_seq >= 0),
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS forge_notification_rules (
    id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    match JSONB NOT NULL DEFAULT '{}',
    sink VARCHAR(64) NOT NULL,
    integration_connection_id TEXT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    quiet_hours JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_forge_notification_rules_owner
    ON forge_notification_rules (owner_id);

CREATE TABLE IF NOT EXISTS forge_notification_deliveries (
    id UUID PRIMARY KEY,
    notification_id UUID NOT NULL REFERENCES forge_notifications(id) ON DELETE CASCADE,
    rule_id UUID NOT NULL REFERENCES forge_notification_rules(id) ON DELETE CASCADE,
    sink VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'delivered', 'failed', 'dead', 'suppressed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    delivered_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (notification_id, rule_id)
);
CREATE INDEX IF NOT EXISTS idx_forge_notification_deliveries_due
    ON forge_notification_deliveries (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_forge_notification_deliveries_rule
    ON forge_notification_deliveries (rule_id);
