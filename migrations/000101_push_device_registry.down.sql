-- Back to tokens in device_tokens. Rows already moved into the credential store
-- have no token here and cannot come back: they are dropped, and those devices
-- register again (every app launch registers its tokens).
DROP INDEX IF EXISTS idx_forge_push_deliveries_tenant_status;
ALTER TABLE forge_push_deliveries DROP COLUMN IF EXISTS tenant_id;

ALTER TABLE device_tokens DROP CONSTRAINT IF EXISTS device_tokens_moved_or_not;
DELETE FROM device_tokens WHERE token IS NULL;
DROP INDEX IF EXISTS idx_device_tokens_unmoved;
DROP INDEX IF EXISTS idx_device_tokens_connection;
DROP INDEX IF EXISTS idx_device_tokens_credential;
DROP INDEX IF EXISTS idx_device_tokens_sha_kind;
CREATE UNIQUE INDEX IF NOT EXISTS idx_device_tokens_token_kind ON device_tokens (token, kind);
ALTER TABLE device_tokens ALTER COLUMN token SET NOT NULL;
ALTER TABLE device_tokens DROP COLUMN IF EXISTS token_sha256;
ALTER TABLE device_tokens DROP COLUMN IF EXISTS credential_name;
ALTER TABLE device_tokens DROP COLUMN IF EXISTS connection_id;
