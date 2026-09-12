DROP INDEX IF EXISTS idx_observatory_fragments_tenant;
ALTER TABLE observatory_fragments DROP COLUMN IF EXISTS owner_id;
ALTER TABLE observatory_fragments DROP COLUMN IF EXISTS tenant_id;
