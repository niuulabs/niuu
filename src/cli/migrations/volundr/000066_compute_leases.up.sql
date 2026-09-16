-- Durable VM allocations. Failed and draining machines still consume pool capacity.
CREATE TABLE IF NOT EXISTS compute_leases (
    id UUID PRIMARY KEY,
    pool_id TEXT NOT NULL,
    session_id UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'provisioning', 'ready', 'busy', 'draining', 'released', 'failed'
    )),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (data->>'id' = id::text),
    CHECK (data->>'pool_id' = pool_id),
    CHECK (data->>'session_id' = session_id::text),
    CHECK (data->>'state' = state)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_leases_live_session
    ON compute_leases (pool_id, session_id) WHERE state != 'released';
CREATE INDEX IF NOT EXISTS idx_compute_leases_pool_state ON compute_leases (pool_id, state);
