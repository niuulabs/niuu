-- Persist the session definition (runtime type, e.g. skuldClaude / skuldGrok) a
-- session was launched with, so restarts re-apply the same transport instead of
-- falling back to the platform default.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_definition VARCHAR(255);
