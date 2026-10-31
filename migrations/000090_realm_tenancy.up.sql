-- Realms belong to a tenant; a slug is unique within one tenant, not globally.
--
-- The backfill only assigns a tenant on unambiguous evidence. A realm with no
-- such evidence keeps tenant_id = '' and is unreachable until an operator
-- assigns it, because guessing could hand a realm to the wrong tenant.
ALTER TABLE realms ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';

-- A bound resident is the strongest evidence. It runs in the tenant that must
-- keep reading this realm's trust grants.
UPDATE realms r
SET tenant_id = bound.tenant_id,
    owner_id = COALESCE(r.owner_id, bound.owner_id)
FROM (
    SELECT realm_id,
           MIN(tenant_id) AS tenant_id,
           CASE WHEN COUNT(DISTINCT owner_id) = 1 THEN MIN(owner_id) END AS owner_id
    FROM resident_runtimes
    WHERE realm_id IS NOT NULL
    GROUP BY realm_id
    HAVING COUNT(DISTINCT tenant_id) = 1
) bound
WHERE r.id = bound.realm_id
  AND r.tenant_id = '';

-- An owner who belongs to exactly one tenant.
UPDATE realms r
SET tenant_id = m.tenant_id
FROM tenant_memberships m
WHERE r.tenant_id = ''
  AND m.user_id = r.owner_id
  AND (SELECT COUNT(*) FROM tenant_memberships m2 WHERE m2.user_id = r.owner_id) = 1;

-- A deployment with one tenant has only one possible answer.
UPDATE realms r
SET tenant_id = only_tenant.id
FROM (SELECT MIN(id) AS id FROM tenants HAVING COUNT(*) = 1) only_tenant
WHERE r.tenant_id = '';

ALTER TABLE realms DROP CONSTRAINT IF EXISTS realms_slug_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_realms_tenant_slug ON realms (tenant_id, slug);
