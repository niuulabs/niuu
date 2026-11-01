-- One persistent coordinator session per tracker ticket. It is created when a
-- ticket first starts and closes with the ticket; workflow runs come and go
-- under it, linked through their campaign's `ticket` metadata.
CREATE TABLE IF NOT EXISTS ticket_coordinators (
    saga_id UUID NOT NULL,
    issue_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL,
    connection_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    PRIMARY KEY (saga_id, issue_id)
);

CREATE INDEX IF NOT EXISTS ticket_coordinators_owner_idx
    ON ticket_coordinators (owner_id, tenant_id);
