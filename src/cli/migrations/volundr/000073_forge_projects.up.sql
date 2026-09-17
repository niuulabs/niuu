-- Additive project coordination. Existing sessions retain NULL coordination.
CREATE TABLE IF NOT EXISTS forge_projects (
    id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL DEFAULT '',
    document JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_forge_projects_scope ON forge_projects(owner_id, tenant_id, updated_at DESC);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS coordination JSONB;
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions ((coordination->>'project_id'))
    WHERE coordination IS NOT NULL;
CREATE TABLE IF NOT EXISTS forge_project_dispatches (
    id UUID PRIMARY KEY,
    fingerprint CHAR(64) NOT NULL,
    session_id UUID NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Dispatch IDs remain as tombstones if their session is explicitly deleted.
CREATE TABLE IF NOT EXISTS forge_project_receipts (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES forge_projects(id),
    seq BIGSERIAL UNIQUE NOT NULL,
    document JSONB NOT NULL,
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_forge_receipts_cursor ON forge_project_receipts(project_id, seq);
