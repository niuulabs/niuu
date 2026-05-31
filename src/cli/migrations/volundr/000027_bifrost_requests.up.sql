-- Bifrost per-request accounting table.
-- One row per LLM request: token counts, cost attribution, and latency.

CREATE TABLE IF NOT EXISTS bifrost_requests (
    id                 BIGSERIAL   PRIMARY KEY,
    request_id         TEXT        NOT NULL DEFAULT '',
    agent_id           TEXT        NOT NULL,
    tenant_id          TEXT        NOT NULL,
    session_id         TEXT        NOT NULL DEFAULT '',
    saga_id            TEXT        NOT NULL DEFAULT '',
    model              TEXT        NOT NULL,
    provider           TEXT        NOT NULL DEFAULT '',
    input_tokens       INTEGER     NOT NULL DEFAULT 0,
    output_tokens      INTEGER     NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER     NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER     NOT NULL DEFAULT 0,
    reasoning_tokens   INTEGER     NOT NULL DEFAULT 0,
    cost_usd           NUMERIC     NOT NULL DEFAULT 0,
    latency_ms         REAL        NOT NULL DEFAULT 0,
    streaming          BOOLEAN     NOT NULL DEFAULT FALSE,
    timestamp          TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bifrost_req_tenant_ts  ON bifrost_requests(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_bifrost_req_agent_ts   ON bifrost_requests(agent_id,  timestamp);
CREATE INDEX IF NOT EXISTS idx_bifrost_req_model      ON bifrost_requests(model);
CREATE INDEX IF NOT EXISTS idx_bifrost_req_provider   ON bifrost_requests(provider);
