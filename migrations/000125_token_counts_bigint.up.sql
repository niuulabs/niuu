-- A session's token total counts cache reads, so a long one passes 2^31 - 1. Every later
-- usage report then failed to bind (asyncpg OverflowError, HTTP 500) and its tokens were lost.
-- chronicles.token_usage is written from sessions.tokens_used and token_usage.tokens from the
-- same reports, so they widen together. Altering a BIGINT column to BIGINT is a no-op.
ALTER TABLE sessions ALTER COLUMN tokens_used TYPE BIGINT;
ALTER TABLE token_usage ALTER COLUMN tokens TYPE BIGINT;
ALTER TABLE chronicles ALTER COLUMN token_usage TYPE BIGINT;
