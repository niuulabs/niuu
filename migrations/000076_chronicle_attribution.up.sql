-- Chronicles outlive their session, so they carry the session's owner and tenant
-- themselves: history that survives a session delete is still scoped to the
-- principals who could list that session. No foreign keys, for the same reason
-- 000038 dropped the session one: history must survive what it references.
ALTER TABLE chronicles ADD COLUMN IF NOT EXISTS owner_id TEXT;
ALTER TABLE chronicles ADD COLUMN IF NOT EXISTS tenant_id TEXT;

UPDATE chronicles c
SET owner_id = s.owner_id, tenant_id = s.tenant_id
FROM sessions s
WHERE c.session_id = s.id AND c.owner_id IS NULL AND c.tenant_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_chronicles_tenant_owner ON chronicles(tenant_id, owner_id);
