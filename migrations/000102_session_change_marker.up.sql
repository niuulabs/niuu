-- Every write to a session row, or to a reader's read state, records the writing
-- transaction. A session list can then answer "what changed since cursor C", where
-- C is the oldest transaction still running when the previous list was read
-- (pg_snapshot_xmin): anything it could not see then has an id >= C. A trigger
-- sets it, so no write path can forget to move it (updated_at is written by hand
-- and several paths, e.g. session usage and work facts, leave it alone).
--
-- No index: the list reads every visible session row anyway, and an index on a
-- column every update changes would end HOT updates on this busy table.
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS change_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
ALTER TABLE session_read_states
    ADD COLUMN IF NOT EXISTS change_xid xid8 NOT NULL DEFAULT pg_current_xact_id();

CREATE OR REPLACE FUNCTION mark_change_xid() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.change_xid := pg_current_xact_id();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sessions_mark_change ON sessions;
CREATE TRIGGER sessions_mark_change
    BEFORE INSERT OR UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION mark_change_xid();

DROP TRIGGER IF EXISTS session_read_states_mark_change ON session_read_states;
CREATE TRIGGER session_read_states_mark_change
    BEFORE INSERT OR UPDATE ON session_read_states
    FOR EACH ROW EXECUTE FUNCTION mark_change_xid();
