-- Revoke scoped credentials before older code can treat them as unrestricted.
-- Preserve the audit record; token_hash no longer matches any presented JWT.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'personal_access_tokens' AND column_name = 'scopes'
    ) THEN
        UPDATE personal_access_tokens
        SET token_hash = 'revoked:' || token_hash
        WHERE scopes IS NOT NULL AND token_hash NOT LIKE 'revoked:%';
    END IF;
END $$;
DROP INDEX IF EXISTS idx_pats_tenant_owner;
ALTER TABLE personal_access_tokens DROP COLUMN IF EXISTS scopes;
ALTER TABLE personal_access_tokens DROP COLUMN IF EXISTS tenant_id;
