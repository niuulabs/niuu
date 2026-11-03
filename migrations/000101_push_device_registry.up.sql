-- One push-device registry: integration connections hold the devices, the
-- credential store holds every token, and device_tokens keeps only what the push
-- gateway queries and locks.
--
-- A device is the owner's push-device integration connection (one per app
-- install). Each of its tokens (alert, Live Activity, push-to-start, widget) is a
-- credential-store entry; its device_tokens row names that entry
-- (`credential_name`) and its connection (`connection_id`), and keeps the kind,
-- environment, activity and throttle columns. `token_sha256` lets a token be found
-- and moved between owners without keeping its value.
--
-- Rows written before this migration still hold their token. Forge moves each one
-- into the credential store at startup (creating the owner's connection) and then
-- clears `token`; the CHECK keeps every row either moved or still to move. Only
-- alert tokens can be moved: any other kind names no app install, so the migration
-- refuses while one is stored (the app registers those again with its install).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM device_tokens WHERE token IS NOT NULL AND kind <> 'alert') THEN
        RAISE EXCEPTION USING
            MESSAGE = 'device_tokens holds Live Activity, push-to-start or widget tokens, '
                || 'which name no app install and cannot be moved to the credential store',
            HINT = 'Run DELETE FROM device_tokens WHERE kind <> ''alert''; the app registers '
                || 'them again with its install_id. Then apply this migration again.';
    END IF;
END
$$;

ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS connection_id TEXT;
ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS credential_name TEXT;
ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS token_sha256 TEXT;
ALTER TABLE device_tokens ALTER COLUMN token DROP NOT NULL;

UPDATE device_tokens
SET token_sha256 = encode(sha256(convert_to(token, 'UTF8')), 'hex')
WHERE token_sha256 IS NULL AND token IS NOT NULL;
ALTER TABLE device_tokens ALTER COLUMN token_sha256 SET NOT NULL;

DROP INDEX IF EXISTS idx_device_tokens_token_kind;
CREATE UNIQUE INDEX IF NOT EXISTS idx_device_tokens_sha_kind ON device_tokens (token_sha256, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_device_tokens_credential
    ON device_tokens (credential_name) WHERE credential_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_device_tokens_connection ON device_tokens (connection_id);
CREATE INDEX IF NOT EXISTS idx_device_tokens_unmoved ON device_tokens (id) WHERE token IS NOT NULL;

ALTER TABLE device_tokens DROP CONSTRAINT IF EXISTS device_tokens_moved_or_not;
ALTER TABLE device_tokens ADD CONSTRAINT device_tokens_moved_or_not CHECK (
    (token IS NULL AND credential_name IS NOT NULL AND connection_id IS NOT NULL)
    OR (token IS NOT NULL AND credential_name IS NULL AND connection_id IS NULL)
);

-- Each alert belongs to the notification's org: deliveries, their counts and the
-- gateway's status are per tenant. NULL is an untenanted notification.
ALTER TABLE forge_push_deliveries ADD COLUMN IF NOT EXISTS tenant_id TEXT;
UPDATE forge_push_deliveries
SET tenant_id = NULLIF(payload ->> 'tenant_id', '')
WHERE tenant_id IS NULL AND payload ? 'tenant_id';
CREATE INDEX IF NOT EXISTS idx_forge_push_deliveries_tenant_status
    ON forge_push_deliveries (tenant_id, status);
