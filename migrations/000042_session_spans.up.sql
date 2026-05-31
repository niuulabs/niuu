CREATE TABLE IF NOT EXISTS session_spans (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    trace_id UUID NOT NULL,
    parent_span_id UUID,
    kind VARCHAR(64) NOT NULL,
    name TEXT NOT NULL,
    status VARCHAR(24) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITH TIME ZONE,
    duration_ms INTEGER,
    actor_type VARCHAR(32),
    actor_id TEXT,
    actor_label TEXT,
    source_service VARCHAR(32) NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_spans_session_started
    ON session_spans(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_session_spans_parent
    ON session_spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_session_spans_trace
    ON session_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_session_spans_kind
    ON session_spans(kind);
