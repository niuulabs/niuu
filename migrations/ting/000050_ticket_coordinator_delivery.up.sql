-- What a coordinator still has to be told, and why its last start failed. A
-- brief waits here while its session boots and goes out once the session reports in.
ALTER TABLE ticket_coordinators
    ADD COLUMN IF NOT EXISTS pending_brief TEXT,
    ADD COLUMN IF NOT EXISTS start_error TEXT,
    ADD COLUMN IF NOT EXISTS advanced_from TEXT;

CREATE INDEX IF NOT EXISTS ticket_coordinators_session_idx
    ON ticket_coordinators (session_id);
