-- Resident-owned spans cannot satisfy the original sessions-only constraint.
DELETE FROM session_spans AS span
WHERE NOT EXISTS (
    SELECT 1
    FROM sessions AS session
    WHERE session.id = span.session_id
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'session_spans_session_id_fkey'
          AND conrelid = 'session_spans'::regclass
    ) THEN
        ALTER TABLE session_spans
            ADD CONSTRAINT session_spans_session_id_fkey
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE;
    END IF;
END
$$;
