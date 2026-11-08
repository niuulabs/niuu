-- Which holder owns which mesh peer id. A mesh certificate (niuu.mesh.attestation) binds
-- a peer id to a key; this table stops two live holders from being certified under the
-- same peer id. A claim is one atomic upsert: it succeeds when the peer id is free, held
-- by the same holder (a session restarting on a new launch, a resident reactivating), or
-- expired. expires_at is the expiry of the latest certificate issued under the claim, so a
-- crashed holder frees its peer id one certificate lifetime after its last renewal.
CREATE TABLE IF NOT EXISTS mesh_peer_claims (
    peer_id    TEXT PRIMARY KEY,
    holder     TEXT NOT NULL,
    launch_id  TEXT NOT NULL,
    mesh_realm TEXT NOT NULL DEFAULT '',
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mesh_peer_claims_holder
    ON mesh_peer_claims (holder, launch_id);
