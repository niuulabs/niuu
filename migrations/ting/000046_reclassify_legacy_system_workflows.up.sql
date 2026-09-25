-- Reclassify pre-#1012 system workflow rows as package-seeded ('bundled'),
-- not operator-authored ('authored').
--
-- Before PR #1012, seed_system_workflows() upserted every scope='system' row
-- from the packaged bundle on every process start and deleted unknown system
-- rows, so on any install that predates it, every scope='system' row is
-- exactly package content -- there was no "authored system workflow" concept
-- yet. Migration 000044 then added version_origin with
-- "DEFAULT 'authored'", which backfills every pre-existing row (system and
-- user scope alike) to 'authored', mislabeling those legacy system rows.
--
-- Effects of the mislabeling if left uncorrected: on the PostgreSQL path,
-- the new seed_system_workflows() treats an 'authored' row as a protected
-- operator successor and never updates it again (silent staleness, and it
-- stops being read-only); on the file-backed migration path
-- (migrate_workflow_catalog), it is reported as diverging from its packaged
-- definition even though its content is identical, blocking cutover.
--
-- The predicate below identifies rows that can only be legacy pre-#1012
-- system content, never a genuine post-#1012 authored row:
--   * scope = 'system' -- user-scope rows are never bundled and are left
--     alone.
--   * version_origin = 'authored' -- only rows the 000044 backfill touched;
--     rows already 'bundled' need no change.
--   * based_on_revision IS NULL -- every save that can produce an 'authored'
--     row post-#1012 (PostgresWorkflowRepository._advance) always sets
--     based_on_revision to the prior document's revision. A legacy row was
--     never processed by that code path, so the column -- added by 000044
--     with no backfill value -- is NULL.
--   * NOT EXISTS a workflow_versions row -- the workflow_versions table was
--     created by 000044. PostgresWorkflowRepository archives a snapshot into
--     it on every save, including a brand-new row's own initial creation
--     (save_workflow's `current is None` branch still calls _archive right
--     after _write_workflow). A row saved at any point through the new code
--     therefore always has at least one workflow_versions entry. A legacy
--     row predates that table's existence and structurally cannot have one.
-- All four conditions must hold together, so a genuine post-#1012 authored
-- row (which always gets a workflow_versions entry on save) is never caught.
UPDATE workflows
SET version_origin = 'bundled'
WHERE scope = 'system'
  AND version_origin = 'authored'
  AND based_on_revision IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM workflow_versions v WHERE v.workflow_id = workflows.id
  );
