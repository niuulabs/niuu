-- Forge session spans key on trace_id = the session UUID, a stable per-session
-- correlation id predating OTel adoption here. It is not a W3C trace id and
-- cannot become one without breaking every existing consumer of
-- GET /sessions/{id}/trace. Store the W3C trace id (32 lowercase hex chars)
-- alongside instead, when the span was recorded while a real OTel trace was
-- active, so a Forge span can be cross-referenced with the OTLP trace it
-- belongs to.
ALTER TABLE session_spans ADD COLUMN IF NOT EXISTS w3c_trace_id VARCHAR(32);

CREATE INDEX IF NOT EXISTS idx_session_spans_w3c_trace_id
    ON session_spans(w3c_trace_id)
    WHERE w3c_trace_id IS NOT NULL;
