-- Explicit realm<->resident link, by id, instead of the naming convention
-- the web join previously relied on (resident named after the realm slug).
--
-- ON DELETE RESTRICT, not SET NULL: the realms REST route's own contract
-- says the resident must be torn down first ("The realm's resident is a
-- Ravn fleet object and is removed through DELETE /api/v1/ravn/ravens/{id}"
-- in rest_realms.py), but nothing enforced it. SET NULL would silently
-- unbind an still-live resident from its realm on delete -- a fallback, not
-- an answer. RESTRICT makes an out-of-order delete fail loudly instead.
ALTER TABLE resident_runtimes
    ADD COLUMN IF NOT EXISTS realm_id UUID REFERENCES realms(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_resident_runtimes_realm
    ON resident_runtimes (realm_id)
    WHERE realm_id IS NOT NULL;

-- Backfill the explicit link for existing residents that were only ever
-- matched by the (now-secondary) naming convention: a resident's name
-- equals its realm's slug (web-next's residentNameFor). Only backfill an
-- UNAMBIGUOUS match -- one resident, one realm, same name/slug, resident
-- not already bound to some other realm -- so we never guess.
UPDATE resident_runtimes rr
SET realm_id = r.id
FROM realms r
WHERE rr.realm_id IS NULL
  AND rr.name = r.slug
  AND (SELECT COUNT(*) FROM resident_runtimes rr2 WHERE rr2.name = r.slug) = 1
  AND (SELECT COUNT(*) FROM realms r2 WHERE r2.slug = rr.name) = 1;
