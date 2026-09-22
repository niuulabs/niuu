DROP TABLE IF EXISTS session_read_states;
ALTER TABLE sessions DROP COLUMN IF EXISTS latest_final_at;
ALTER TABLE sessions DROP COLUMN IF EXISTS latest_final_turn_id;
ALTER TABLE sessions DROP COLUMN IF EXISTS latest_final_seq;
