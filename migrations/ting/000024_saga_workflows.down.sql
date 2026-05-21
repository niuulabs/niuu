DROP INDEX IF EXISTS idx_sagas_workflow_id;
ALTER TABLE sagas DROP COLUMN IF EXISTS workflow_snapshot;
ALTER TABLE sagas DROP COLUMN IF EXISTS workflow_version;
ALTER TABLE sagas DROP COLUMN IF EXISTS workflow_id;
