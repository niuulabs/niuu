-- Revert owner_id on sagas, restore user_id on integration_connections

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'integration_connections' AND column_name = 'owner_id'
    ) THEN
        ALTER TABLE integration_connections RENAME COLUMN owner_id TO user_id;
    END IF;
END $$;

DROP INDEX IF EXISTS idx_sagas_owner_id;
ALTER TABLE sagas DROP COLUMN IF EXISTS owner_id;
