-- Session analytics events can belong to a Forge session or resident runtime.
-- The REST adapter authorizes either subject before accepting events.
ALTER TABLE session_events
    DROP CONSTRAINT IF EXISTS session_events_session_id_fkey;
