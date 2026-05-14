-- Add feature_branch column to sagas (used by dispatch to set working branch)
ALTER TABLE sagas ADD COLUMN IF NOT EXISTS feature_branch TEXT NOT NULL DEFAULT 'main';
