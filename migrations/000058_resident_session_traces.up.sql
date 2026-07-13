-- A trace subject can be either a Forge session or a resident runtime. Access
-- checks at the trace API validate the subject before spans are accepted.
ALTER TABLE session_spans
    DROP CONSTRAINT IF EXISTS session_spans_session_id_fkey;
