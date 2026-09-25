-- `niuu join` — machines (K8s clusters, DGX Sparks, laptops in mini/docker
-- mode) joined to Guild via a single-use pairing code, and the nodes that
-- results in. See docs/operator/joining-machines.md.

-- Server-side record of a minted pairing code. The code itself is a scoped
-- workload JWT (token_use=valkyrie_build, scopes=["node_join"]) so entry to
-- the join route is gated by the existing require_scope("node_join")
-- machinery; this table exists in addition to that so the code can be spent
-- exactly once even though the JWT itself stays structurally valid until it
-- expires (see .claude/rules/architecture.md).
CREATE TABLE IF NOT EXISTS niuu_pairing_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by_node_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_niuu_pairing_codes_expires
    ON niuu_pairing_codes(expires_at);

-- A machine joined through a pairing code. Node-originated calls
-- (heartbeat/leave) are authenticated by an Ed25519 signature checked
-- against public_key, never a bearer JWT.
CREATE TABLE IF NOT EXISTS niuu_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    -- Base64-encoded raw 32-byte Ed25519 public key.
    public_key TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ,
    -- Strictly-increasing replay-protection watermark (Unix seconds) for
    -- signed node requests. NULL before the node's first signed call.
    last_request_at BIGINT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_niuu_nodes_public_key
    ON niuu_nodes(public_key);

CREATE INDEX IF NOT EXISTS idx_niuu_nodes_tenant
    ON niuu_nodes(tenant_id);
