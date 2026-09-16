-- Durable dispatch identities: pending claims are never silently re-executed.
CREATE TABLE IF NOT EXISTS session_message_deliveries (
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    request_id VARCHAR(200) NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    claim_token UUID NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'failed')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, request_id)
);
