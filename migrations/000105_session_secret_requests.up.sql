-- Secure secret requests: an agent asks its session's owner for one secret, the owner
-- types the value into a masked card, and the agent claims it once. The table holds the
-- request and its state only; the value lives in the credential store (OpenBao on a
-- cluster) under the owner and is never written here.
-- No FK to sessions: like forge_presented_files, the rows are removed by the session
-- service, which must first delete the session-only values they point at.
CREATE TABLE IF NOT EXISTS session_secret_requests (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    target TEXT NULL,
    status VARCHAR(16) NOT NULL
        CHECK (status IN ('pending', 'fulfilled', 'delivered', 'declined', 'cancelled')),
    keep VARCHAR(16) NULL CHECK (keep IN ('session', 'saved')),
    credential_name TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ NULL,
    delivered_at TIMESTAMPTZ NULL
);

-- One open (pending or fulfilled-unclaimed) request per session and name: a repeat ask
-- keeps waiting on the request already standing.
CREATE UNIQUE INDEX IF NOT EXISTS idx_session_secret_requests_open
    ON session_secret_requests (session_id, name)
    WHERE status IN ('pending', 'fulfilled');

CREATE INDEX IF NOT EXISTS idx_session_secret_requests_session_created
    ON session_secret_requests (session_id, created_at DESC);
