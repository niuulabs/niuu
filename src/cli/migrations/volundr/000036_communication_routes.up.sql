CREATE TABLE IF NOT EXISTS communication_routes (
    id UUID PRIMARY KEY,
    platform TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    thread_id TEXT,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'room',
    default_target TEXT,
    active BOOLEAN NOT NULL DEFAULT true,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_comm_routes_session_id
    ON communication_routes(session_id);

CREATE INDEX IF NOT EXISTS idx_comm_routes_owner_id
    ON communication_routes(owner_id);

CREATE INDEX IF NOT EXISTS idx_comm_routes_active_lookup
    ON communication_routes(platform, conversation_id, thread_id, active);
