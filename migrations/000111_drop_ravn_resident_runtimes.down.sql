-- The up migration deleted the resident_runtimes rows of the removed 'ravn' engine; their
-- content (backend references, endpoints, usage, flock membership) cannot be restored,
-- and the engine no longer exists to run them. This migration is deliberately a no-op.
SELECT 1;
