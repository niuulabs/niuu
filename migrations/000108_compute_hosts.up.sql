-- Registered hosts: machines that join a Forge's compute pool as fixed inventory, and the
-- single-use codes an admin mints for them to join with. See docs/design/bm-compute-hosts.md.
CREATE TABLE IF NOT EXISTS compute_hosts (
    id UUID PRIMARY KEY,
    pool_id TEXT NOT NULL,
    name TEXT NOT NULL,
    host_public_key TEXT NOT NULL,
    allocation_id UUID,
    health TEXT NOT NULL CHECK (health IN ('pending', 'healthy', 'quarantined', 'unreachable')),
    service TEXT NOT NULL CHECK (service IN ('active', 'draining', 'out', 'removing')),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (data->>'id' = id::text),
    CHECK (data->>'pool_id' = pool_id),
    CHECK (data->>'name' = name),
    CHECK (data->>'host_public_key' = host_public_key),
    CHECK (data->>'health' = health),
    CHECK (data->>'service' = service),
    CHECK (data->>'allocation_id' IS NOT DISTINCT FROM allocation_id::text)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_hosts_name ON compute_hosts (pool_id, name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_hosts_public_key ON compute_hosts (host_public_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_hosts_allocation
    ON compute_hosts (allocation_id) WHERE allocation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_compute_hosts_pool ON compute_hosts (pool_id);

CREATE TABLE IF NOT EXISTS compute_host_pairing_codes (
    id UUID PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    pool_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by_host_id UUID,
    revoked_at TIMESTAMPTZ,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (data->>'id' = id::text),
    CHECK (data->>'code_hash' = code_hash),
    CHECK (data->>'pool_id' = pool_id)
);
CREATE INDEX IF NOT EXISTS idx_compute_host_pairing_codes_pool
    ON compute_host_pairing_codes (pool_id);
