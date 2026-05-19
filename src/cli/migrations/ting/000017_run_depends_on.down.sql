-- Remove depends_on column from run_progress and runs.

ALTER TABLE run_progress DROP COLUMN IF EXISTS depends_on;
ALTER TABLE runs DROP COLUMN IF EXISTS depends_on;
