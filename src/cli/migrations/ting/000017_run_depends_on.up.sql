-- Add depends_on column to run_progress for inter-run dependency tracking.
-- Values are run names (not IDs) within the same saga.

ALTER TABLE run_progress ADD COLUMN IF NOT EXISTS depends_on TEXT[] DEFAULT '{}';

-- Also add to the runs table used by NativeAdapter.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS depends_on TEXT[] DEFAULT '{}';
