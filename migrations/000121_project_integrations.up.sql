-- Team integrations: a connection a Forge project owns instead of one person.
-- A project connection's owner_id is `project:<project id>` and its credential is
-- stored under the credential owner type `project`, so no per-person lookup ever
-- finds it. `connected_by` is the owner who installed it.
ALTER TABLE integration_connections ADD COLUMN IF NOT EXISTS project_id UUID;
ALTER TABLE integration_connections ADD COLUMN IF NOT EXISTS connected_by TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_integration_connections_project
    ON integration_connections(project_id) WHERE project_id IS NOT NULL;

-- Who used a team connection, for whom, and for what.
CREATE TABLE IF NOT EXISTS integration_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id UUID NOT NULL,
    project_id UUID NOT NULL,
    acting_for TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_integration_usage_project
    ON integration_usage(project_id, occurred_at DESC);
