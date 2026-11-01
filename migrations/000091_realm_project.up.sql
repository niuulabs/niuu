-- The Forge project a realm belongs to. Several realms can share one; a realm
-- without one stands alone.
ALTER TABLE realms ADD COLUMN IF NOT EXISTS project_id UUID;
CREATE INDEX IF NOT EXISTS idx_realms_project_id ON realms (project_id);
