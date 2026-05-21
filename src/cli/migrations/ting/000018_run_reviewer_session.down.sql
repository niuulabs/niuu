DROP INDEX IF EXISTS idx_runs_reviewer_session;
ALTER TABLE runs DROP COLUMN IF EXISTS review_round;
ALTER TABLE runs DROP COLUMN IF EXISTS reviewer_session_id;
ALTER TABLE run_progress DROP COLUMN IF EXISTS review_round;
ALTER TABLE run_progress DROP COLUMN IF EXISTS reviewer_session_id;
