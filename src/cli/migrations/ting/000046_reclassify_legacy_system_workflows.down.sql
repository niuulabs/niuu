-- Reverse 000046: restore version_origin = 'authored' on exactly the rows
-- this migration's up() would have flipped to 'bundled' -- and nothing else.
-- A row a legitimate save later marked 'bundled' (e.g. package reseeding a
-- version bump) always carries a non-NULL based_on_revision and a
-- workflow_versions entry, so it is excluded here just as it was excluded
-- from the up() UPDATE.
UPDATE workflows
SET version_origin = 'authored'
WHERE scope = 'system'
  AND version_origin = 'bundled'
  AND based_on_revision IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM workflow_versions v WHERE v.workflow_id = workflows.id
  );
