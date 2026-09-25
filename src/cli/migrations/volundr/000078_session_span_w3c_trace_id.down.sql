DROP INDEX IF EXISTS idx_session_spans_w3c_trace_id;
ALTER TABLE session_spans DROP COLUMN IF EXISTS w3c_trace_id;
