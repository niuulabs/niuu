CREATE TABLE IF NOT EXISTS credential_enrollments (
    id                  UUID         PRIMARY KEY,
    connection_id       UUID         NOT NULL REFERENCES integration_connections(id) ON DELETE CASCADE,
    owner_id             TEXT         NOT NULL,
    tenant_id            TEXT         NOT NULL,
    provider_slug        VARCHAR(100) NOT NULL,
    credential_name      VARCHAR(253) NOT NULL,
    method               VARCHAR(64)  NOT NULL,
    state                VARCHAR(32)  NOT NULL,
    runner_ref           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    verification_uri     TEXT         NOT NULL DEFAULT '',
    user_code            VARCHAR(128) NOT NULL DEFAULT '',
    expires_at           TIMESTAMPTZ  NOT NULL,
    error_code           VARCHAR(100) NOT NULL DEFAULT '',
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT credential_enrollments_state_check CHECK (
        state IN ('pending', 'awaiting_user', 'complete', 'failed', 'expired', 'cancelled')
    )
);

CREATE INDEX IF NOT EXISTS idx_credential_enrollments_owner
    ON credential_enrollments (owner_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_credential_enrollments_active_connection
    ON credential_enrollments (connection_id)
    WHERE state IN ('pending', 'awaiting_user');
