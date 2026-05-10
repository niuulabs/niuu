-- Restore session foreign keys for chronicles and timeline events

ALTER TABLE chronicles
    ADD CONSTRAINT chronicles_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL;

ALTER TABLE chronicle_events
    ADD CONSTRAINT chronicle_events_session_id_fkey
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE;
