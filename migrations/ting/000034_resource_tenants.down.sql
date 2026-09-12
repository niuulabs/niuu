DROP INDEX IF EXISTS idx_workflow_campaigns_tenant_owner;
DROP INDEX IF EXISTS idx_workflows_tenant_owner;
DROP INDEX IF EXISTS idx_sagas_tenant_owner;
ALTER TABLE workflow_campaigns DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE workflows DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE sagas DROP COLUMN IF EXISTS tenant_id;
