-- Migration: add structured_outcome and outcome_event_type to raids
-- Stores the parsed outcome block from a completed Ravn session.

ALTER TABLE raids ADD COLUMN IF NOT EXISTS structured_outcome JSONB;
ALTER TABLE raids ADD COLUMN IF NOT EXISTS outcome_event_type TEXT;
