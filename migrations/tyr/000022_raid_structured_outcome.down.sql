-- Rollback: remove structured_outcome and outcome_event_type from raids

ALTER TABLE raids DROP COLUMN IF EXISTS structured_outcome;
ALTER TABLE raids DROP COLUMN IF EXISTS outcome_event_type;
