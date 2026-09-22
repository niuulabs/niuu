-- Inbox attention is independent of lifecycle, and is scoped to the authenticated reader.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS latest_final_seq BIGINT NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS latest_final_turn_id TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS latest_final_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS session_read_states (
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    read_through_seq BIGINT NOT NULL DEFAULT 0 CHECK (read_through_seq >= 0),
    manually_unread BOOLEAN NOT NULL DEFAULT FALSE,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    PRIMARY KEY (session_id, user_id)
);
