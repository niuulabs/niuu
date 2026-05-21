-- Add identifier and url columns to runs for tracker display.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS identifier TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN IF NOT EXISTS url TEXT NOT NULL DEFAULT '';
