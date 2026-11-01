-- Restoring the global constraint fails loudly if two tenants share a slug.
DROP INDEX IF EXISTS idx_realms_tenant_slug;
ALTER TABLE realms ADD CONSTRAINT realms_slug_key UNIQUE (slug);
ALTER TABLE realms DROP COLUMN IF EXISTS tenant_id;
