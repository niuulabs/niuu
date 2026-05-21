ALTER TABLE sagas ADD COLUMN IF NOT EXISTS workflow_id UUID;
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS workflow_version TEXT;
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS workflow_snapshot JSONB;

CREATE INDEX IF NOT EXISTS idx_sagas_workflow_id
    ON sagas(workflow_id)
    WHERE workflow_id IS NOT NULL;
