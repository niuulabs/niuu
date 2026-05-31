-- Add base_branch column to sagas
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS base_branch TEXT NOT NULL DEFAULT 'main';
