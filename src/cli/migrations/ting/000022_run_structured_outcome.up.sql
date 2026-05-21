-- Migration: add structured_outcome and outcome_event_type to runs
-- Stores the parsed outcome block from a completed Ravn session.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS structured_outcome JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS outcome_event_type TEXT;
