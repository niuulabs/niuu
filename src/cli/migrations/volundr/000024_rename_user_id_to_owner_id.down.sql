-- Revert owner_id back to user_id in integration_connections

ALTER TABLE integration_connections RENAME COLUMN owner_id TO user_id;

DROP INDEX IF EXISTS idx_integration_connections_owner;
CREATE INDEX IF NOT EXISTS idx_integration_connections_user
    ON integration_connections(user_id, integration_type);
