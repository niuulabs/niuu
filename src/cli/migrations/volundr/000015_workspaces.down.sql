-- Rollback workspaces table and user provisioning columns

ALTER TABLE sessions DROP COLUMN IF EXISTS workspace_id;

ALTER TABLE users DROP COLUMN IF EXISTS provision_error;
ALTER TABLE users DROP COLUMN IF EXISTS provisioned_at;

DROP TABLE IF EXISTS workspaces;
