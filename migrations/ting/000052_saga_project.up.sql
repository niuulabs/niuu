-- The Forge project a tracker link belongs to, when it joined one that already
-- existed. A project can hold several links; a row without one stands on its own id.
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS project_id UUID;
CREATE INDEX IF NOT EXISTS idx_sagas_project_id ON sagas(project_id);
