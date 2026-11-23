-- Fails with "integer out of range", changing nothing, when a stored count no longer fits:
-- clipping it would silently falsify the session's usage.
ALTER TABLE chronicles ALTER COLUMN token_usage TYPE INTEGER;
ALTER TABLE token_usage ALTER COLUMN tokens TYPE INTEGER;
ALTER TABLE sessions ALTER COLUMN tokens_used TYPE INTEGER;
