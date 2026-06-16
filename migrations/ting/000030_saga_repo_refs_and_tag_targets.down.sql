DROP INDEX IF EXISTS idx_sagas_target_tags;

ALTER TABLE sagas DROP COLUMN IF EXISTS target_match;
ALTER TABLE sagas DROP COLUMN IF EXISTS target_tags;
ALTER TABLE sagas DROP COLUMN IF EXISTS repo_branches;
