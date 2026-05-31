ALTER TABLE runs ADD COLUMN IF NOT EXISTS reviewer_session_id TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS review_round INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_runs_reviewer_session ON runs (reviewer_session_id) WHERE reviewer_session_id IS NOT NULL;

ALTER TABLE run_progress ADD COLUMN IF NOT EXISTS reviewer_session_id TEXT;
ALTER TABLE run_progress ADD COLUMN IF NOT EXISTS review_round INTEGER NOT NULL DEFAULT 0;
