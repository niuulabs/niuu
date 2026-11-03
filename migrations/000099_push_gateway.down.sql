DROP TABLE IF EXISTS forge_push_deliveries;
DROP TABLE IF EXISTS forge_push_gateway_state;

-- Back to one alert token per (owner, token): the other kinds did not exist.
DROP INDEX IF EXISTS idx_device_tokens_push_due;
DROP INDEX IF EXISTS idx_device_tokens_owner_kind;
DROP INDEX IF EXISTS idx_device_tokens_token_kind;
DELETE FROM device_tokens WHERE kind <> 'alert';
ALTER TABLE device_tokens DROP CONSTRAINT IF EXISTS device_tokens_owner_id_token_key;
ALTER TABLE device_tokens ADD CONSTRAINT device_tokens_owner_id_token_key UNIQUE (owner_id, token);
ALTER TABLE device_tokens DROP COLUMN IF EXISTS push_due;
ALTER TABLE device_tokens DROP COLUMN IF EXISTS last_push_at;
ALTER TABLE device_tokens DROP COLUMN IF EXISTS activity_id;
ALTER TABLE device_tokens DROP COLUMN IF EXISTS environment;
ALTER TABLE device_tokens DROP COLUMN IF EXISTS kind;
