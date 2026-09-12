-- Legacy fragments remain quarantined until ownership is audited.
ALTER TABLE observatory_fragments ADD COLUMN IF NOT EXISTS owner_id TEXT NOT NULL DEFAULT '';
ALTER TABLE observatory_fragments ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_observatory_fragments_tenant ON observatory_fragments(tenant_id);
