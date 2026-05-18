-- Rollback tenant hierarchy

ALTER TABLE sessions DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE sessions DROP COLUMN IF EXISTS owner_id;

DROP TABLE IF EXISTS tenant_memberships;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS tenants;
