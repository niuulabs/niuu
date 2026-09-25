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
    -- Operator consent, recorded at mint time, for the joining node to
    -- register a plaintext (http://) instance URL. A node can never grant
    -- itself this — only whoever minted the code decides.
    allow_plaintext BOOLEAN NOT NULL DEFAULT false,
    -- Operator consent, recorded at mint time, for a node that does not
    -- itself verify caller identity (host_auth.mode: none) to join a Guild
    -- that runs host_auth.mode: oidc. Without this, Guild forwards a
    -- caller's real bearer token to an instance that trusts everyone —
    -- an explicit, admin-granted exception, never a node self-declaration.
    allow_untrusted_node_auth BOOLEAN NOT NULL DEFAULT false,
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
    -- Copied once from the pairing code that admitted this node (never
    -- writable afterward — there is no node-update endpoint at all) so
    -- every later heartbeat re-registration enforces the same
    -- operator-granted transport consent, not a value the node supplies.
    allow_plaintext BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ,
    -- Strictly-increasing replay-protection watermark (Unix MILLISECONDS,
    -- not seconds — a heartbeat and a leave issued within the same second
    -- must both be able to advance it) for signed node requests. NULL
    -- before the node's first signed call. Advanced only by the single
    -- atomic conditional UPDATE in PostgresNodeRepository.try_advance_watermark;
    -- never read-then-written from application code.
    last_request_at BIGINT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_niuu_nodes_public_key
    ON niuu_nodes(public_key);

CREATE INDEX IF NOT EXISTS idx_niuu_nodes_tenant
    ON niuu_nodes(tenant_id);

-- Node display names are shown to admins choosing what to revoke, so a
-- second node cannot masquerade as an existing one within the same tenant.
CREATE UNIQUE INDEX IF NOT EXISTS idx_niuu_nodes_tenant_name
    ON niuu_nodes(tenant_id, name);

-- The real ownership link for instances a node registers. This column is
-- never exposed on InstanceCreateRequest/InstanceUpdateRequest, so the
-- authenticated PATCH /api/v1/niuu/instances/{id} route can never set or
-- clear it — only GuildJoinRepository (join/heartbeat) writes it. A slug
-- derived from the operator-chosen node *name* must never be the ownership
-- key (a node named to collide with an existing slug must not be able to
-- adopt that row) — node_id is a Guild-assigned UUID the node never
-- chooses, so it cannot be gamed the same way.
ALTER TABLE niuu_instances ADD COLUMN IF NOT EXISTS node_id UUID
    REFERENCES niuu_nodes(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_niuu_instances_node_id
    ON niuu_instances(node_id);

-- One row per (node, kind): concurrent heartbeats deciding "no existing row
-- for this kind, insert a new one" at the same time would otherwise both
-- succeed and leave two rows for the same node+kind. The second concurrent
-- INSERT now fails fast on this constraint instead of silently duplicating.
CREATE UNIQUE INDEX IF NOT EXISTS idx_niuu_instances_node_kind
    ON niuu_instances(node_id, kind) WHERE node_id IS NOT NULL;
