-- Stages of a session's latest launch, as its runtime backend reported them.
-- Written only by the startup recorder; lifecycle updates never touch it.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS startup JSONB;
