-- Bind imported sagas to a specific integration connection. Provider type is
-- descriptive and is not sufficient when an owner has multiple Jira/Linear accounts.
ALTER TABLE sagas
    ADD COLUMN IF NOT EXISTS tracker_connection_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_sagas_tracker_connection
    ON sagas (owner_id, tracker_connection_id, tracker_id);

ALTER TABLE run_progress
    ADD COLUMN IF NOT EXISTS tracker_connection_id TEXT NOT NULL DEFAULT '';
ALTER TABLE run_confidence_events
    ADD COLUMN IF NOT EXISTS tracker_connection_id TEXT NOT NULL DEFAULT '';
ALTER TABLE run_session_messages
    ADD COLUMN IF NOT EXISTS tracker_connection_id TEXT NOT NULL DEFAULT '';

ALTER TABLE run_progress DROP CONSTRAINT IF EXISTS run_progress_pkey;
ALTER TABLE run_progress
    ADD CONSTRAINT run_progress_pkey PRIMARY KEY (tracker_connection_id, tracker_id);

CREATE INDEX IF NOT EXISTS idx_run_progress_connection_status
    ON run_progress (tracker_connection_id, status);
CREATE INDEX IF NOT EXISTS idx_run_progress_connection_session
    ON run_progress (tracker_connection_id, session_id)
    WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_run_confidence_events_connection_tracker
    ON run_confidence_events (tracker_connection_id, tracker_id);
CREATE INDEX IF NOT EXISTS idx_run_session_messages_connection_tracker
    ON run_session_messages (tracker_connection_id, tracker_id);
