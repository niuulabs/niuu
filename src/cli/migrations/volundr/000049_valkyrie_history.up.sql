CREATE TABLE IF NOT EXISTS valkyrie_decisions (
    decision_id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL DEFAULT '',
    valkyrie_id TEXT NOT NULL DEFAULT '',
    operational_state TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT '',
    action_authority TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    correlation_id TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    review_item_id TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_valkyrie_decisions_env_decided
    ON valkyrie_decisions (environment_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_valkyrie_decisions_correlation
    ON valkyrie_decisions (correlation_id);

CREATE TABLE IF NOT EXISTS valkyrie_actions (
    event_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL DEFAULT '',
    environment_id TEXT NOT NULL DEFAULT '',
    valkyrie_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_valkyrie_actions_correlation
    ON valkyrie_actions (correlation_id);
CREATE INDEX IF NOT EXISTS idx_valkyrie_actions_env_observed
    ON valkyrie_actions (environment_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS valkyrie_signals (
    signal_id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'info',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_valkyrie_signals_env_received
    ON valkyrie_signals (environment_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_valkyrie_signals_severity
    ON valkyrie_signals (severity, received_at DESC);
