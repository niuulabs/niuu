-- Workloads (e.g. a resident's ServiceAccount) allowed to act for one owner: the
-- workload exchange's owner_from: delegation mappings admit only these.
CREATE TABLE IF NOT EXISTS workload_delegations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The exchange verifier that pins the workload's cluster signing keys.
    verifier TEXT NOT NULL,
    -- The workload proof's subject, e.g. system:serviceaccount:skuld:resident-<id>.
    subject TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    resident_id TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_workload_delegations_active
    ON workload_delegations (verifier, subject)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_workload_delegations_tenant_owner
    ON workload_delegations (tenant_id, owner_id)
    WHERE revoked_at IS NULL;
