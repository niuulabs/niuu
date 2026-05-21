DROP INDEX IF EXISTS idx_sagas_instance_id;

ALTER TABLE sagas DROP COLUMN IF EXISTS instance_id;
