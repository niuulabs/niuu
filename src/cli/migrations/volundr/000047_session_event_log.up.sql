-- Durable, append-only, full-fidelity per-session event log.
--
-- Unlike session_events (analytics previews, CASCADE-deleted with the session),
-- this table preserves the COMPLETE agent output verbatim — assistant text,
-- thinking, tool_use, tool_result, deltas — ordered by a monotonic per-session
-- `seq`. It is the source of truth for transcript replay: any client (web or
-- iOS) can resume from a cursor (`seq`) and reconstruct the full conversation
-- regardless of whether a socket was attached when the events were produced.
--
-- The producer (skuld) assigns `seq` monotonically per session (it is the sole
-- writer for a session's CLI), so the PRIMARY KEY (session_id, seq) makes
-- ingestion idempotent under at-least-once retries (ON CONFLICT DO NOTHING).
--
-- No FK to sessions: the log survives session deletion (same posture as
-- chronicle_events after migration 000038) so history is never lost.

CREATE TABLE IF NOT EXISTS session_event_log (
    session_id   UUID NOT NULL,
    seq          BIGINT NOT NULL,
    kind         VARCHAR(64) NOT NULL,
    role         VARCHAR(32),
    request_id   TEXT,
    payload      JSONB NOT NULL DEFAULT '{}',
    ts           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_session_event_log_session_seq
    ON session_event_log(session_id, seq);

CREATE INDEX IF NOT EXISTS idx_session_event_log_request
    ON session_event_log(session_id, request_id);
