-- Preserve chronicles and timeline history after session deletion

ALTER TABLE chronicles
    DROP CONSTRAINT IF EXISTS chronicles_session_id_fkey;

ALTER TABLE chronicle_events
    DROP CONSTRAINT IF EXISTS chronicle_events_session_id_fkey;
