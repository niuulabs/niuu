-- Existing records have no authoritative tenant attribution. Do not infer it
-- from a request. They remain inaccessible under Cedar until audited/backfilled.
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE workflow_campaigns ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_sagas_tenant_owner ON sagas(tenant_id, owner_id);
CREATE INDEX IF NOT EXISTS idx_workflows_tenant_owner ON workflows(tenant_id, owner_id);
CREATE INDEX IF NOT EXISTS idx_workflow_campaigns_tenant_owner ON workflow_campaigns(tenant_id, owner_id);
