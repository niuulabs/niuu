-- A notification can be resolved (LEX-1442): an attention or critical stops alarming once
-- the incident is over. resolved_at says when, resolved_by_notification_id names the
-- notification that resolved it (a milestone with the same correlation_id, or one that
-- listed it in `resolves`), and resolution is the operator's free text when resolved by
-- hand. No default, so adding the columns rewrites nothing.
ALTER TABLE forge_notifications ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ NULL;
ALTER TABLE forge_notifications ADD COLUMN IF NOT EXISTS resolved_by_notification_id UUID NULL;
ALTER TABLE forge_notifications ADD COLUMN IF NOT EXISTS resolution TEXT NULL;

-- Auto-resolve looks up an owner's unresolved notifications by correlation id.
CREATE INDEX IF NOT EXISTS idx_forge_notifications_unresolved_correlation
    ON forge_notifications (owner_id, correlation_id)
    WHERE resolved_at IS NULL AND correlation_id IS NOT NULL;
