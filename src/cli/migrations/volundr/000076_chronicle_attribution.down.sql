DROP INDEX IF EXISTS idx_chronicles_tenant_owner;
ALTER TABLE chronicles DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE chronicles DROP COLUMN IF EXISTS owner_id;
