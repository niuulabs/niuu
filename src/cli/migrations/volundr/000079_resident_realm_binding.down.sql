DROP INDEX IF EXISTS idx_resident_runtimes_realm;
ALTER TABLE resident_runtimes DROP COLUMN IF EXISTS realm_id;
