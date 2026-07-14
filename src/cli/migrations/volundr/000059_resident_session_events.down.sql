-- Resident-owned events cannot satisfy the original sessions-only constraint.
DELETE FROM session_events AS event
WHERE NOT EXISTS (
    SELECT 1
    FROM sessions AS session
    WHERE session.id = event.session_id
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'session_events_session_id_fkey'
          AND conrelid = 'session_events'::regclass
    ) THEN
        ALTER TABLE session_events
            ADD CONSTRAINT session_events_session_id_fkey
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE;
    END IF;
END
$$;
