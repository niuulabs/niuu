-- Bifrost per-request audit log table.
-- One row per LLM request attempt: outcome, rule matches, and security metadata.

CREATE TABLE IF NOT EXISTS bifrost_audit (
    id               BIGSERIAL   PRIMARY KEY,
    request_id       TEXT        NOT NULL DEFAULT '',
    agent_id         TEXT        NOT NULL,
    tenant_id        TEXT        NOT NULL,
    session_id       TEXT        NOT NULL DEFAULT '',
    saga_id          TEXT        NOT NULL DEFAULT '',
    model            TEXT        NOT NULL,
    provider         TEXT        NOT NULL DEFAULT '',
    outcome          TEXT        NOT NULL DEFAULT 'success',
    status_code      INTEGER     NOT NULL DEFAULT 200,
    rule_name        TEXT        NOT NULL DEFAULT '',
    rule_action      TEXT        NOT NULL DEFAULT '',
    tags             JSONB       NOT NULL DEFAULT '{}',
    error_message    TEXT        NOT NULL DEFAULT '',
    latency_ms       REAL        NOT NULL DEFAULT 0,
    tokens_input     INTEGER     NOT NULL DEFAULT 0,
    tokens_output    INTEGER     NOT NULL DEFAULT 0,
    cost_usd         REAL        NOT NULL DEFAULT 0,
    cache_hit        BOOLEAN     NOT NULL DEFAULT FALSE,
    prompt_content   TEXT        NOT NULL DEFAULT '',
    response_content TEXT        NOT NULL DEFAULT '',
    timestamp        TIMESTAMPTZ NOT NULL
);

-- Idempotent column additions for existing deployments upgraded from pre-NIU-462 schema.
ALTER TABLE bifrost_audit ADD COLUMN IF NOT EXISTS tokens_input     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bifrost_audit ADD COLUMN IF NOT EXISTS tokens_output    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bifrost_audit ADD COLUMN IF NOT EXISTS cost_usd         REAL    NOT NULL DEFAULT 0;
ALTER TABLE bifrost_audit ADD COLUMN IF NOT EXISTS cache_hit        BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bifrost_audit ADD COLUMN IF NOT EXISTS prompt_content   TEXT    NOT NULL DEFAULT '';
ALTER TABLE bifrost_audit ADD COLUMN IF NOT EXISTS response_content TEXT    NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_bifrost_audit_tenant_ts  ON bifrost_audit(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_bifrost_audit_agent_ts   ON bifrost_audit(agent_id,  timestamp);
CREATE INDEX IF NOT EXISTS idx_bifrost_audit_outcome    ON bifrost_audit(outcome);
CREATE INDEX IF NOT EXISTS idx_bifrost_audit_request_id ON bifrost_audit(request_id);
