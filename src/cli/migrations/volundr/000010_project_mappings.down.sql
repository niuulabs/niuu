-- Rollback project_mappings table and tracker_issue_id column

ALTER TABLE sessions DROP COLUMN IF EXISTS tracker_issue_id;

DROP TABLE IF EXISTS project_mappings;
