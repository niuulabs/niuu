-- Add reason column to runs for reject context
ALTER TABLE runs ADD COLUMN IF NOT EXISTS reason TEXT;
