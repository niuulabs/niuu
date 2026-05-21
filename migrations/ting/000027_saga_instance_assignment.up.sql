ALTER TABLE sagas ADD COLUMN IF NOT EXISTS instance_id UUID;

CREATE INDEX IF NOT EXISTS idx_sagas_instance_id ON sagas(instance_id);
