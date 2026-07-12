CREATE TABLE IF NOT EXISTS resident_runtimes (
    id              UUID        PRIMARY KEY,
    owner_id        TEXT        NOT NULL,
    tenant_id       TEXT        NOT NULL,
    name            VARCHAR(255) NOT NULL,
    persona_name    VARCHAR(255) NOT NULL DEFAULT '',
    model           VARCHAR(255) NOT NULL DEFAULT '',
    backend         VARCHAR(32) NOT NULL,
    engine          VARCHAR(32) NOT NULL,
    profile_id      VARCHAR(100) NOT NULL,
    desired_state   VARCHAR(32) NOT NULL DEFAULT 'running',
    observed_state  VARCHAR(32) NOT NULL DEFAULT 'pending',
    backend_ref     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    endpoints       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    capabilities    TEXT[]      NOT NULL DEFAULT '{}',
    conditions      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_id, name)
);

CREATE INDEX IF NOT EXISTS idx_resident_runtimes_tenant_owner
    ON resident_runtimes (tenant_id, owner_id);
CREATE INDEX IF NOT EXISTS idx_resident_runtimes_observed_state
    ON resident_runtimes (observed_state);
CREATE INDEX IF NOT EXISTS idx_resident_runtimes_profile
    ON resident_runtimes (profile_id);
