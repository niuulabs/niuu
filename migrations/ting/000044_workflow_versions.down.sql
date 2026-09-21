DROP TABLE IF EXISTS workflow_versions;
ALTER TABLE workflows DROP COLUMN IF EXISTS based_on_revision;
ALTER TABLE workflows DROP COLUMN IF EXISTS version_origin;
