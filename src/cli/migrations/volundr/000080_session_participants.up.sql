-- Durable per-session collaboration grants: several participants share one
-- agent room in the browser through DURABLE grants exposed as Cedar
-- attributes (room_viewers/room_approvers), computed from the
-- ACTIVE, unexpired rows here. Cascades with the session it grants access
-- to, unlike chronicles (000076), which are deliberately kept alive after
-- their session is gone.
CREATE TABLE IF NOT EXISTS session_participants (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'invited',
    expires_at TIMESTAMPTZ,
    invited_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    UNIQUE (session_id, user_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'session_participants_role_check'
    ) THEN
        ALTER TABLE session_participants
            ADD CONSTRAINT session_participants_role_check
            CHECK (role IN ('observer', 'teacher', 'debugger', 'approver'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'session_participants_status_check'
    ) THEN
        ALTER TABLE session_participants
            ADD CONSTRAINT session_participants_status_check
            CHECK (status IN ('invited', 'active', 'revoked'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_session_participants_session ON session_participants(session_id);
CREATE INDEX IF NOT EXISTS idx_session_participants_user ON session_participants(user_id);
