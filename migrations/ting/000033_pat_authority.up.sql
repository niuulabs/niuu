-- Existing unattributed PATs remain recorded but are not valid credentials.
-- Rotate them through the authenticated issuance path to establish tenant/scope metadata.
ALTER TABLE personal_access_tokens ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE personal_access_tokens ADD COLUMN IF NOT EXISTS scopes TEXT[];
ALTER TABLE personal_access_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_pats_tenant_owner ON personal_access_tokens(tenant_id, owner_id);
