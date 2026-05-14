-- PR tracking fields on runs

ALTER TABLE runs ADD COLUMN IF NOT EXISTS pr_url TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS pr_id  TEXT;
CREATE INDEX IF NOT EXISTS idx_runs_session_id ON runs(session_id)
    WHERE session_id IS NOT NULL;
