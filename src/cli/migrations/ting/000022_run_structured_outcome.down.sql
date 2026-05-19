-- Rollback: remove structured_outcome and outcome_event_type from runs

ALTER TABLE runs DROP COLUMN IF EXISTS structured_outcome;
ALTER TABLE runs DROP COLUMN IF EXISTS outcome_event_type;
