-- Rollback chronicles table

DROP INDEX IF EXISTS idx_chronicles_parent_id;
DROP INDEX IF EXISTS idx_chronicles_tags;
DROP INDEX IF EXISTS idx_chronicles_created_at;
DROP INDEX IF EXISTS idx_chronicles_repo;
DROP INDEX IF EXISTS idx_chronicles_project;
DROP INDEX IF EXISTS idx_chronicles_session_id;
DROP TABLE IF EXISTS chronicles;
