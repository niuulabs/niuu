DROP TRIGGER IF EXISTS session_read_states_mark_change ON session_read_states;
DROP TRIGGER IF EXISTS sessions_mark_change ON sessions;
DROP FUNCTION IF EXISTS mark_change_xid();
ALTER TABLE session_read_states DROP COLUMN IF EXISTS change_xid;
ALTER TABLE sessions DROP COLUMN IF EXISTS change_xid;
